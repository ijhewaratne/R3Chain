"""Local store.

Holds everything a result depends on, in a form that cannot change afterwards:

* source files, addressed by checksum
* dataset and policy records, one per identifier and version
* scenario revisions, addressed by the hash of their content
* runs and results
* the stage cache, addressed by the hash of a stage's inputs

Metadata lives in SQLite and files live beside it. The same interface can later be served by
PostgreSQL with PostGIS (extension point X14).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from r3chain.domain.hashing import bytes_checksum, content_hash, file_checksum, short_hash
from r3chain.domain.paths import canonical_path, diff_json
from r3chain.domain.records.runs import (
    Policy,
    PolicyRef,
    Result,
    Run,
    RunStatus,
    StageOutput,
)
from r3chain.domain.records.scenario import (
    ChangeKind,
    FieldChange,
    Scenario,
    ScenarioRevision,
)
from r3chain.domain.records.study import Dataset

_TABLES = """
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id TEXT NOT NULL, version TEXT NOT NULL, record_json TEXT NOT NULL,
    PRIMARY KEY (dataset_id, version));
CREATE TABLE IF NOT EXISTS policies (
    policy_id TEXT NOT NULL, version TEXT NOT NULL, record_json TEXT NOT NULL,
    PRIMARY KEY (policy_id, version));
CREATE TABLE IF NOT EXISTS revisions (
    revision_id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL, created_at TEXT NOT NULL,
    record_json TEXT NOT NULL, scenario_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL, record_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS results (
    result_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, record_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stage_cache (
    cache_key TEXT PRIMARY KEY, stage TEXT NOT NULL, output_json TEXT NOT NULL,
    run_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY, study_id TEXT NOT NULL, project_id TEXT NOT NULL,
    status TEXT NOT NULL, created_at TEXT NOT NULL, record_json TEXT NOT NULL);
"""


class ImmutabilityError(Exception):
    """An attempt to change something that is already stored under the same identity."""


class NotFoundError(KeyError):
    """The requested record is not in the store."""


def utc_now() -> datetime:
    return datetime.now(UTC)


class Store:
    def __init__(self, root: Path, clock: Callable[[], datetime] = utc_now) -> None:
        self.root = root
        self._clock = clock
        root.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(root / "r3chain.db", timeout=30.0)
        self._db.execute("PRAGMA busy_timeout = 30000;")
        self._db.executescript(_TABLES)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # --- internal helpers -------------------------------------------------------------------

    def _one(self, sql: str, parameters: tuple[Any, ...]) -> tuple[Any, ...] | None:
        row = self._db.execute(sql, parameters).fetchone()
        return tuple(row) if row is not None else None

    def _insert_once(
        self, table: str, key: dict[str, str], columns: dict[str, str], what: str
    ) -> bool:
        """Insert a record. Storing identical content again is a no-op. Storing different
        content under the same key is refused. Returns True when a row was written."""
        where = " AND ".join(f"{name} = ?" for name in key)
        existing = self._one(
            f"SELECT record_json FROM {table} WHERE {where}",
            tuple(key.values()),
        )
        if existing is not None:
            if existing[0] != columns["record_json"]:
                raise ImmutabilityError(
                    f"{what} already exists with different content. Create a new version instead"
                )
            return False
        names = [*key, *columns]
        placeholders = ", ".join("?" for _ in names)
        self._db.execute(
            f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})",
            (*key.values(), *columns.values()),
        )
        self._db.commit()
        return True

    # --- files addressed by checksum --------------------------------------------------------

    def _blob_path(self, checksum: str) -> Path:
        digest = short_hash(checksum, 64)
        return self.root / "blobs" / digest[:2] / digest

    def put_bytes(self, data: bytes) -> str:
        checksum = bytes_checksum(data)
        path = self._blob_path(checksum)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return checksum

    def put_file(self, path: Path) -> str:
        return self.put_bytes(path.read_bytes())

    def has_blob(self, checksum: str) -> bool:
        return self._blob_path(checksum).exists()

    def read_bytes(self, checksum: str) -> bytes:
        path = self._blob_path(checksum)
        if not path.exists():
            raise NotFoundError(f"no stored file with checksum {checksum}")
        data = path.read_bytes()
        if bytes_checksum(data) != checksum:
            raise ImmutabilityError(f"stored file {checksum} no longer matches its checksum")
        return data

    # --- datasets ---------------------------------------------------------------------------

    def register_dataset(self, dataset: Dataset, file: Path | None = None) -> None:
        """Register one dataset version. When the file is given it must match the checksum."""
        if file is not None:
            actual = file_checksum(file)
            if actual != dataset.checksum:
                raise ImmutabilityError(
                    f"dataset {dataset.dataset_id} {dataset.version}: file checksum {actual} "
                    f"differs from the recorded checksum {dataset.checksum}"
                )
            self.put_file(file)
        self._insert_once(
            "datasets",
            {"dataset_id": dataset.dataset_id, "version": dataset.version},
            {"record_json": dataset.model_dump_json()},
            f"dataset {dataset.dataset_id} version {dataset.version}",
        )

    def get_dataset(self, dataset_id: str, version: str) -> Dataset:
        row = self._one(
            "SELECT record_json FROM datasets WHERE dataset_id = ? AND version = ?",
            (dataset_id, version),
        )
        if row is None:
            raise NotFoundError(f"dataset {dataset_id} version {version}")
        return Dataset.model_validate_json(row[0])

    # --- policies ---------------------------------------------------------------------------

    def register_policy(self, policy: Policy) -> PolicyRef:
        """Store a policy version once. The same content under a newer schema version is the same
        policy. Different content under the same version is refused."""
        try:
            stored = self.get_policy(policy.policy_id, policy.version)
        except NotFoundError:
            stored = None
        if stored is not None:
            if stored != policy:
                raise ImmutabilityError(
                    f"policy {policy.policy_id} version {policy.version} already exists with "
                    "different content. Create a new version instead"
                )
            return policy_ref(policy)
        self._insert_once(
            "policies",
            {"policy_id": policy.policy_id, "version": policy.version},
            {"record_json": policy.model_dump_json()},
            f"policy {policy.policy_id} version {policy.version}",
        )
        return policy_ref(policy)

    def get_policy(self, policy_id: str, version: str) -> Policy:
        row = self._one(
            "SELECT record_json FROM policies WHERE policy_id = ? AND version = ?",
            (policy_id, version),
        )
        if row is None:
            raise NotFoundError(f"policy {policy_id} version {version}")
        return Policy.model_validate_json(row[0])

    # --- scenario revisions -----------------------------------------------------------------

    def add_revision(
        self,
        scenario: Scenario,
        *,
        author: str,
        rationale: str,
        parent_revision_id: str | None = None,
    ) -> ScenarioRevision:
        """Freeze a scenario as a revision. Identical content always gives the same revision."""
        revision_id = scenario.revision_id()
        existing = self._one(
            "SELECT record_json FROM revisions WHERE revision_id = ?", (revision_id,)
        )
        if existing is not None:
            return ScenarioRevision.model_validate_json(existing[0])
        changes: tuple[FieldChange, ...] = ()
        if parent_revision_id is not None:
            parent = self.get_scenario(parent_revision_id)
            changes = tuple(
                change.model_copy(
                    update={
                        "field_path": canonical_path(
                            parent if change.kind is ChangeKind.REMOVED else scenario,
                            change.field_path,
                        )
                    }
                )
                for change in diff_json(parent.to_json_dict(), scenario.to_json_dict())
            )
        revision = ScenarioRevision(
            revision_id=revision_id,
            scenario_id=scenario.scenario_id,
            scenario_hash=scenario.content_hash(),
            parent_revision_id=parent_revision_id,
            created_at=self._clock(),
            author=author,
            rationale=rationale,
            changes=changes,
        )
        self._db.execute(
            "INSERT INTO revisions (revision_id, scenario_id, created_at, record_json, "
            "scenario_json) VALUES (?, ?, ?, ?, ?)",
            (
                revision_id,
                scenario.scenario_id,
                revision.created_at.isoformat(),
                revision.model_dump_json(),
                scenario.model_dump_json(),
            ),
        )
        self._db.commit()
        return revision

    def get_revision(self, revision_id: str) -> ScenarioRevision:
        row = self._one("SELECT record_json FROM revisions WHERE revision_id = ?", (revision_id,))
        if row is None:
            raise NotFoundError(f"revision {revision_id}")
        return ScenarioRevision.model_validate_json(row[0])

    def get_scenario(self, revision_id: str) -> Scenario:
        row = self._one("SELECT scenario_json FROM revisions WHERE revision_id = ?", (revision_id,))
        if row is None:
            raise NotFoundError(f"revision {revision_id}")
        scenario = Scenario.model_validate_json(row[0])
        if scenario.revision_id() != revision_id:
            raise ImmutabilityError(f"stored revision {revision_id} no longer matches its hash")
        return scenario

    def list_revisions(self, scenario_id: str) -> tuple[ScenarioRevision, ...]:
        rows = self._db.execute(
            "SELECT record_json FROM revisions WHERE scenario_id = ? ORDER BY created_at, rowid",
            (scenario_id,),
        ).fetchall()
        return tuple(ScenarioRevision.model_validate_json(row[0]) for row in rows)

    def list_all_revisions(self) -> tuple[ScenarioRevision, ...]:
        rows = self._db.execute(
            "SELECT record_json FROM revisions ORDER BY created_at, rowid"
        ).fetchall()
        return tuple(ScenarioRevision.model_validate_json(row[0]) for row in rows)

    def latest_revision_for_study(self, study_id: str) -> tuple[ScenarioRevision, Scenario]:
        rows = self._db.execute(
            "SELECT record_json, scenario_json FROM revisions ORDER BY rowid DESC"
        ).fetchall()
        for rev_json, scen_json in rows:
            scenario = Scenario.model_validate_json(scen_json)
            if scenario.study.study_id == study_id or scenario.scenario_id == study_id:
                return ScenarioRevision.model_validate_json(rev_json), scenario
        raise NotFoundError(f"study {study_id}")

    # --- runs and results -------------------------------------------------------------------

    def create_run(self, run: Run) -> None:
        if run.status is not RunStatus.RUNNING:
            raise ValueError("a run is created in the running state")
        self._insert_once(
            "runs",
            {"run_id": run.run_id},
            {
                "revision_id": run.scenario_revision,
                "status": run.status.value,
                "created_at": run.created_at.isoformat(),
                "record_json": run.model_dump_json(),
            },
            f"run {run.run_id}",
        )

    def finalize_run(self, run: Run) -> None:
        """Write the final state of a run exactly once."""
        if run.status is RunStatus.RUNNING:
            raise ValueError("a finalized run cannot still be running")
        cursor = self._db.execute(
            "UPDATE runs SET status = ?, record_json = ? WHERE run_id = ? AND status = ?",
            (run.status.value, run.model_dump_json(), run.run_id, RunStatus.RUNNING.value),
        )
        self._db.commit()
        if cursor.rowcount != 1:
            raise ImmutabilityError(f"run {run.run_id} is not running and cannot be changed")

    def get_run(self, run_id: str) -> Run:
        row = self._one("SELECT record_json FROM runs WHERE run_id = ?", (run_id,))
        if row is None:
            raise NotFoundError(f"run {run_id}")
        return Run.model_validate_json(row[0])

    def list_runs(self, revision_id: str | None = None) -> tuple[Run, ...]:
        if revision_id is None:
            rows = self._db.execute(
                "SELECT record_json FROM runs ORDER BY created_at, rowid"
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT record_json FROM runs WHERE revision_id = ? ORDER BY created_at, rowid",
                (revision_id,),
            ).fetchall()
        return tuple(Run.model_validate_json(row[0]) for row in rows)

    def add_result(self, result: Result) -> None:
        self._insert_once(
            "results",
            {"result_id": result.result_id},
            {"run_id": result.run_id, "record_json": result.model_dump_json()},
            f"result {result.result_id}",
        )

    def get_results(self, run_id: str) -> tuple[Result, ...]:
        rows = self._db.execute(
            "SELECT record_json FROM results WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
        return tuple(Result.model_validate_json(row[0]) for row in rows)

    def get_result(self, result_id: str) -> Result:
        row = self._one("SELECT record_json FROM results WHERE result_id = ?", (result_id,))
        if row is None:
            raise NotFoundError(f"result {result_id}")
        return Result.model_validate_json(row[0])

    # --- jobs -------------------------------------------------------------------------------

    def put_job_raw(
        self,
        *,
        job_id: str,
        study_id: str,
        project_id: str,
        status: str,
        created_at: str,
        record_json: str,
    ) -> None:
        self._insert_once(
            "jobs",
            {"job_id": job_id},
            {
                "study_id": study_id,
                "project_id": project_id,
                "status": status,
                "created_at": created_at,
                "record_json": record_json,
            },
            f"job {job_id}",
        )

    def update_job_raw(self, job_id: str, status: str, record_json: str) -> None:
        cursor = self._db.execute(
            "UPDATE jobs SET status = ?, record_json = ? WHERE job_id = ?",
            (status, record_json, job_id),
        )
        self._db.commit()
        if cursor.rowcount != 1:
            raise NotFoundError(f"job {job_id}")

    def get_job_raw(self, job_id: str) -> str:
        row = self._one("SELECT record_json FROM jobs WHERE job_id = ?", (job_id,))
        if row is None:
            raise NotFoundError(f"job {job_id}")
        return str(row[0])

    def list_jobs_raw(self, study_id: str | None = None) -> tuple[str, ...]:
        if study_id is None:
            rows = self._db.execute(
                "SELECT record_json FROM jobs ORDER BY created_at, rowid"
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT record_json FROM jobs WHERE study_id = ? ORDER BY created_at, rowid",
                (study_id,),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    # --- stage cache ------------------------------------------------------------------------

    def cache_get(self, cache_key: str) -> tuple[StageOutput, str] | None:
        row = self._one(
            "SELECT output_json, run_id FROM stage_cache WHERE cache_key = ?", (cache_key,)
        )
        if row is None:
            return None
        return StageOutput.model_validate_json(row[0]), str(row[1])

    def cache_put(self, cache_key: str, output: StageOutput, run_id: str) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO stage_cache (cache_key, stage, output_json, run_id) "
            "VALUES (?, ?, ?, ?)",
            (cache_key, output.stage, output.model_dump_json(), run_id),
        )
        self._db.commit()


def policy_ref(policy: Policy) -> PolicyRef:
    """A reference to one policy version.

    The hash covers what the policy says, not the schema version it is stored under, so a schema
    upgrade does not change the identity of an unchanged policy.
    """
    content = policy.to_json_dict()
    content.pop("schema_version", None)
    return PolicyRef(
        policy_id=policy.policy_id, version=policy.version, policy_hash=content_hash(content)
    )


def load_policy_file(path: Path) -> Policy:
    return Policy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_policies(directory: Path) -> tuple[Policy, ...]:
    """Load every policy file below a directory, in a stable order."""
    if not directory.exists():
        return ()
    return tuple(load_policy_file(path) for path in sorted(directory.rglob("*.yaml")))
