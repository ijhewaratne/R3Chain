"""The pipeline runner.

A run evaluates one frozen scenario revision through an ordered chain of stages.

Dependency hashing
    Each stage declares exactly which inputs it depends on. The cache key of a stage is the hash
    of those inputs, the stage version and the outputs of the stages it requires. A change to an
    input therefore recomputes the stages that depend on it and nothing else, and a result can
    never be reused after one of its inputs changed.

Failure categories
    A stage that needs an input which is unknown or in conflict ends as ``not_established``.
    Stages after a stage without a usable output end as ``not_run``. An unexpected exception is
    recorded as ``tool_failure``. None of these is ever reported as a physical result.

Replay
    A run can be executed again from its frozen revision without the cache. The deterministic
    hash of the result must be identical.
"""

from __future__ import annotations

import platform
import secrets
import subprocess
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import r3chain
from r3chain.domain.base import Record
from r3chain.domain.evidence import NotEstablished
from r3chain.domain.hashing import content_hash
from r3chain.domain.records.runs import (
    Policy,
    Result,
    Run,
    RunStatus,
    StageOutput,
    StageRecord,
)
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import RankingEligibility, StageStatus, summarise_stage_statuses
from r3chain.domain.validation import ValidationReport, validate_scenario
from r3chain.store.store import Store, policy_ref, utc_now

# Access context is part of every cache key (specification 19.3). Project scoped access arrives
# with the application services in milestone M6.
ACCESS_CONTEXT_LOCAL = "local"

# Outcomes that are a deterministic function of the inputs and may be reused.
_CACHEABLE = frozenset(
    {
        StageStatus.COMPLETED,
        StageStatus.NOT_ESTABLISHED,
        StageStatus.PHYSICAL_LIMIT_EXCEEDED,
        StageStatus.UNSUPPORTED_MODEL,
        StageStatus.INVALID_INPUT,
        StageStatus.NON_CONVERGED,
    }
)


@dataclass(frozen=True)
class StageContext:
    scenario: Scenario
    revision_id: str
    policies: Mapping[str, Policy]
    seed: int
    upstream: Mapping[str, StageOutput]


class Stage(Protocol):
    """One step of the evaluation chain."""

    name: str
    version: str
    requires: tuple[str, ...]
    # A stochastic stage depends on the run seed, a deterministic one does not.
    stochastic: bool

    def input_fingerprint(self, context: StageContext) -> Any:
        """Return, as JSON compatible data, every input this stage depends on."""
        ...

    def run(self, context: StageContext) -> StageOutput: ...


class ScenarioNotExecutableError(Exception):
    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__(f"scenario has {len(report.errors)} validation error(s)")


class ReplayOutcome(Record):
    original_run_id: str
    replay_run_id: str
    identical: bool
    original_hash: str | None
    replay_hash: str | None
    engine_versions_changed: tuple[str, ...] = ()


def code_version() -> str:
    """Package version plus the git commit when the code runs from a checkout."""
    version = r3chain.__version__
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return version
    return f"{version}+{commit}{'.dirty' if dirty else ''}"


def _check_order(stages: Sequence[Stage]) -> None:
    seen: set[str] = set()
    for stage in stages:
        if stage.name in seen:
            raise ValueError(f"stage {stage.name!r} appears twice")
        missing = [name for name in stage.requires if name not in seen]
        if missing:
            raise ValueError(f"stage {stage.name!r} requires {missing}, which do not run before it")
        seen.add(stage.name)


class PipelineRunner:
    def __init__(
        self,
        store: Store,
        stages: Sequence[Stage],
        *,
        clock: Callable[[], datetime] = utc_now,
        code_version_text: str | None = None,
    ) -> None:
        _check_order(stages)
        self._store = store
        self._stages = tuple(stages)
        self._clock = clock
        self._code_version = code_version_text or code_version()

    def engine_versions(self) -> dict[str, str]:
        versions = {"r3chain": r3chain.__version__, "python": platform.python_version()}
        versions.update({f"stage:{stage.name}": stage.version for stage in self._stages})
        return versions

    def _policies(self, scenario: Scenario) -> dict[str, Policy]:
        policies: dict[str, Policy] = {}
        for reference in scenario.policies:
            policy = self._store.get_policy(reference.policy_id, reference.version)
            if policy_ref(policy).policy_hash != reference.policy_hash:
                raise ValueError(
                    f"policy {reference.policy_id} version {reference.version} does not match "
                    "the hash recorded in the scenario"
                )
            policies[reference.policy_id] = policy
        return policies

    def _cache_key(self, stage: Stage, context: StageContext) -> str:
        return content_hash(
            {
                "stage": stage.name,
                "version": stage.version,
                "inputs": stage.input_fingerprint(context),
                "upstream": {
                    name: context.upstream[name].content_hash() for name in stage.requires
                },
                "seed": context.seed if stage.stochastic else None,
                "access_context": ACCESS_CONTEXT_LOCAL,
            }
        )

    def _execute(self, stage: Stage, context: StageContext, raise_on_error: bool) -> StageOutput:
        try:
            output = stage.run(context)
        except NotEstablished as missing:
            return StageOutput(
                stage=stage.name,
                stage_version=stage.version,
                status=StageStatus.NOT_ESTABLISHED,
                reason="required inputs are not established",
                missing_inputs=missing.missing,
            )
        except Exception as error:
            if raise_on_error:
                raise
            return StageOutput(
                stage=stage.name,
                stage_version=stage.version,
                status=StageStatus.TOOL_FAILURE,
                reason=f"{type(error).__name__}: {error}"[:500],
                diagnostics=(traceback.format_exc()[-2000:],),
            )
        if output.stage != stage.name or output.stage_version != stage.version:
            raise ValueError(f"stage {stage.name!r} returned an output labelled {output.stage!r}")
        return output

    def run(
        self,
        revision_id: str,
        *,
        seed: int = 0,
        use_cache: bool = True,
        parent_run_ids: tuple[str, ...] = (),
        raise_on_error: bool = False,
    ) -> tuple[Run, Result]:
        scenario = self._store.get_scenario(revision_id)
        report = validate_scenario(scenario)
        if not report.executable:
            raise ScenarioNotExecutableError(report)
        policies = self._policies(scenario)

        created_at = self._clock()
        run_id = f"run-{created_at:%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
        running = Run(
            run_id=run_id,
            scenario_revision=revision_id,
            input_hashes={},
            code_version=self._code_version,
            engine_versions=self.engine_versions(),
            seed=seed,
            status=RunStatus.RUNNING,
            created_at=created_at,
            parent_run_ids=parent_run_ids,
        )
        self._store.create_run(running)

        outputs: dict[str, StageOutput] = {}
        records: list[StageRecord] = []
        input_hashes: dict[str, str] = {}
        reused_from: set[str] = set()

        for stage in self._stages:
            started = time.perf_counter()
            blocked = [
                name for name in stage.requires if outputs[name].status is not StageStatus.COMPLETED
            ]
            cache_key = ""
            cache_hit = False
            source_run: str | None = None
            if blocked:
                first = blocked[0]
                output = StageOutput(
                    stage=stage.name,
                    stage_version=stage.version,
                    status=StageStatus.NOT_RUN,
                    reason=f"upstream stage {first} ended as {outputs[first].status.value}",
                )
            else:
                context = StageContext(scenario, revision_id, policies, seed, dict(outputs))
                cache_key = self._cache_key(stage, context)
                input_hashes[stage.name] = cache_key
                cached = self._store.cache_get(cache_key) if use_cache else None
                if cached is not None:
                    output, source_run = cached
                    cache_hit = True
                    reused_from.add(source_run)
                else:
                    output = self._execute(stage, context, raise_on_error)
                    if output.status in _CACHEABLE:
                        self._store.cache_put(cache_key, output, run_id)
            outputs[stage.name] = output
            records.append(
                StageRecord(
                    stage=stage.name,
                    stage_version=stage.version,
                    status=output.status,
                    cache_key=cache_key,
                    cache_hit=cache_hit,
                    reused_from_run_id=source_run,
                    output_hash=output.content_hash(),
                    duration_s=time.perf_counter() - started,
                )
            )

        technical, evaluation = summarise_stage_statuses(item.status for item in outputs.values())
        result = Result(
            result_id=run_id.replace("run-", "res-", 1),
            run_id=run_id,
            scenario_revision=revision_id,
            subject="study",
            synthetic=(
                scenario.study.synthetic
                or report.contains_synthetic_values
                or any(output.synthetic for output in outputs.values())
            ),
            technical_status=technical,
            evaluation_status=evaluation,
            ranking_eligibility=RankingEligibility(
                eligible=False,
                reasons=("a study level result is not a ranked candidate",),
            ),
            stage_outputs=tuple(outputs[stage.name] for stage in self._stages),
        )
        finished = running.model_copy(
            update={
                "status": RunStatus.COMPLETED,
                "finished_at": self._clock(),
                "input_hashes": input_hashes,
                "stages": tuple(records),
                "result_hash": result.deterministic_hash(),
                "parent_run_ids": tuple(sorted({*parent_run_ids, *reused_from})),
            }
        )
        self._store.add_result(result)
        for subject_result in self._subject_results(outputs, result):
            self._store.add_result(subject_result)
        self._store.finalize_run(finished)
        return finished, result

    @staticmethod
    def _subject_results(outputs: Mapping[str, StageOutput], study: Result) -> list[Result]:
        """Results for the subjects a stage reports on, for example one per candidate.

        A stage reports them in its payload under ``subject_results``. Each becomes a result
        record of the run with its own four statuses.
        """
        found: list[Result] = []
        for output in outputs.values():
            if output.status is not StageStatus.COMPLETED:
                continue
            for item in output.payload.get("subject_results", []):
                found.append(
                    Result.model_validate(
                        {
                            **item,
                            "result_id": f"{study.result_id}-{item['subject']}",
                            "run_id": study.run_id,
                            "scenario_revision": study.scenario_revision,
                            "synthetic": study.synthetic or bool(item.get("synthetic")),
                        }
                    )
                )
        return found

    def replay(self, run_id: str) -> ReplayOutcome:
        """Execute a stored run again from its frozen revision, without the cache."""
        original = self._store.get_run(run_id)
        replayed, _ = self.run(
            original.scenario_revision,
            seed=original.seed,
            use_cache=False,
            parent_run_ids=(run_id,),
        )
        changed = tuple(
            sorted(
                name
                for name in {*original.engine_versions, *replayed.engine_versions}
                if original.engine_versions.get(name) != replayed.engine_versions.get(name)
            )
        )
        return ReplayOutcome(
            original_run_id=run_id,
            replay_run_id=replayed.run_id,
            identical=original.result_hash == replayed.result_hash,
            original_hash=original.result_hash,
            replay_hash=replayed.result_hash,
            engine_versions_changed=changed,
        )
