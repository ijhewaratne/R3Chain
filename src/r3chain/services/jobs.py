"""Background job manager with stage progress tracking and cooperative cancellation."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from r3chain.domain.records.runs import Run, RunStatus, StageRecord
from r3chain.domain.status import StageStatus
from r3chain.pipeline.runner import PipelineRunner, Stage, code_version
from r3chain.pipeline.stages import default_stages
from r3chain.services.contracts import (
    FailureClass,
    JobRecord,
    JobStatus,
    classify_result_failure,
)
from r3chain.store.store import Store, utc_now


class JobManager:
    """Executes frozen scenario evaluation jobs and tracks live stage progress."""

    def __init__(
        self,
        store_root: Path,
        *,
        stages_factory: Callable[[], Sequence[Stage]] = default_stages,
        clock: Callable[[], datetime] = utc_now,
        code_version_text: str | None = None,
        max_workers: int = 2,
    ) -> None:
        self._store_root = store_root
        self._stages_factory = stages_factory
        self._clock = clock
        self._code_version = code_version_text or code_version()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="r3job")
        self._lock = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._futures: dict[str, Future[JobRecord]] = {}

    def close(self) -> None:
        self._executor.shutdown(wait=True)

    def _save_job(self, store: Store, job: JobRecord, *, create: bool = False) -> JobRecord:
        with self._lock:
            payload = job.model_dump_json()
            if create:
                store.put_job_raw(
                    job_id=job.job_id,
                    study_id=job.study_id,
                    project_id=job.project_id,
                    status=job.status.value,
                    created_at=job.created_at.isoformat(),
                    record_json=payload,
                )
            else:
                store.update_job_raw(job.job_id, job.status.value, payload)
        return job

    def get_job(self, store: Store, job_id: str) -> JobRecord:
        with self._lock:
            raw = store.get_job_raw(job_id)
        return JobRecord.model_validate_json(raw)

    def list_jobs(self, store: Store, study_id: str | None = None) -> tuple[JobRecord, ...]:
        with self._lock:
            rows = store.list_jobs_raw(study_id)
        return tuple(JobRecord.model_validate_json(raw) for raw in rows)

    def cancel_job(self, store: Store, job_id: str) -> JobRecord:
        event = self._cancel_events.get(job_id)
        if event is not None:
            event.set()
        current = self.get_job(store, job_id)
        if current.status in {JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED}:
            return current
        now = self._clock()
        new_status = JobStatus.CANCELLED if current.status is JobStatus.QUEUED else current.status
        updated = current.model_copy(
            update={
                "cancel_requested": True,
                "status": new_status,
                "updated_at": now,
                "finished_at": now if new_status is JobStatus.CANCELLED else None,
            }
        )
        return self._save_job(store, updated)

    def wait_for_job(
        self, store: Store, job_id: str, *, timeout_s: float | None = None
    ) -> JobRecord:
        future = self._futures.get(job_id)
        if future is not None:
            future.result(timeout=timeout_s)
        return self.get_job(store, job_id)

    def submit_run_job(
        self,
        store: Store,
        *,
        study_id: str,
        project_id: str,
        revision_id: str,
        principal_id: str,
        access_scope: str,
        seed: int = 0,
        use_cache: bool = True,
        wait: bool = False,
    ) -> JobRecord:
        stages = tuple(self._stages_factory())
        now = self._clock()
        job_id = f"job-{now:%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"
        initial = JobRecord(
            job_id=job_id,
            study_id=study_id,
            project_id=project_id,
            scenario_revision=revision_id,
            principal_id=principal_id,
            access_scope=access_scope,
            status=JobStatus.QUEUED,
            total_stages=len(stages),
            created_at=now,
            updated_at=now,
        )
        self._save_job(store, initial, create=True)
        cancel_event = threading.Event()
        self._cancel_events[job_id] = cancel_event

        def _worker() -> JobRecord:
            worker_store = Store(self._store_root, clock=self._clock)
            try:
                current = self.get_job(worker_store, job_id)
                if cancel_event.is_set() or current.cancel_requested:
                    fin = self._clock()
                    cancelled = current.model_copy(
                        update={
                            "status": JobStatus.CANCELLED,
                            "cancel_requested": True,
                            "updated_at": fin,
                            "finished_at": fin,
                        }
                    )
                    return self._save_job(worker_store, cancelled)

                running = current.model_copy(
                    update={"status": JobStatus.RUNNING, "updated_at": self._clock()}
                )
                current = self._save_job(worker_store, running)

                def _on_run(run: Run) -> None:
                    nonlocal current
                    current = self._save_job(
                        worker_store,
                        current.model_copy(
                            update={"run_id": run.run_id, "updated_at": self._clock()}
                        ),
                    )

                completed_count = 0

                def _on_stage(record: StageRecord, idx: int, total: int) -> None:
                    nonlocal current, completed_count
                    if record.status is StageStatus.CANCELLED:
                        return
                    completed_count += 1
                    frac = min(1.0, completed_count / total) if total > 0 else 1.0
                    current = self._save_job(
                        worker_store,
                        current.model_copy(
                            update={
                                "current_stage": record.stage,
                                "completed_stages": completed_count,
                                "progress_fraction": frac,
                                "updated_at": self._clock(),
                            }
                        ),
                    )

                runner = PipelineRunner(
                    worker_store,
                    stages,
                    clock=self._clock,
                    code_version_text=self._code_version,
                    access_context=access_scope,
                )
                run, result = runner.run(
                    revision_id,
                    seed=seed,
                    use_cache=use_cache,
                    should_cancel=cancel_event.is_set,
                    on_run_created=_on_run,
                    on_stage_complete=_on_stage,
                )
                results = worker_store.get_results(run.run_id)
                result_ids = tuple(item.result_id for item in results)
                fin = self._clock()
                was_cancelled = run.status is RunStatus.CANCELLED or cancel_event.is_set()
                final_status = JobStatus.CANCELLED if was_cancelled else JobStatus.COMPLETED
                fail_cls = None if was_cancelled else classify_result_failure(result)
                done_job = current.model_copy(
                    update={
                        "status": final_status,
                        "cancel_requested": was_cancelled or current.cancel_requested,
                        "run_id": run.run_id,
                        "result_ids": result_ids,
                        "progress_fraction": (
                            1.0
                            if final_status is JobStatus.COMPLETED
                            else current.progress_fraction
                        ),
                        "updated_at": fin,
                        "finished_at": fin,
                        "failure_class": fail_cls,
                    }
                )
                return self._save_job(worker_store, done_job)
            except Exception as error:
                fin = self._clock()
                latest = self.get_job(worker_store, job_id)
                failed = latest.model_copy(
                    update={
                        "status": JobStatus.FAILED,
                        "updated_at": fin,
                        "finished_at": fin,
                        "error_message": f"{type(error).__name__}: {error}"[:500],
                        "failure_class": FailureClass.SERVICE_FAULT,
                        "diagnostics": (str(error),),
                    }
                )
                return self._save_job(worker_store, failed)
            finally:
                worker_store.close()

        if wait:
            return _worker()
        future = self._executor.submit(_worker)
        self._futures[job_id] = future
        return initial
