"""Access control enforcement and cache-scope isolation (A16, specification section 19.1)."""

from __future__ import annotations

from r3chain.domain.base import NonEmptyStr, Record
from r3chain.domain.hashing import content_hash, short_hash
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.records.study import AllowedUse, Dataset
from r3chain.services.contracts import JobRecord
from r3chain.store.store import Store


class AccessDeniedError(PermissionError):
    """Raised whenever a caller lacks permission to view, run, or export a resource."""


class AccessPrincipal(Record):
    """Authenticated caller identity with project and dataset group permissions."""

    principal_id: NonEmptyStr = "local-cli"
    allowed_projects: tuple[str, ...] = ()
    access_groups: tuple[str, ...] = ()
    allow_restricted: bool = False
    allow_synthetic: bool = True

    @classmethod
    def local_default(cls) -> AccessPrincipal:
        """Default interactive CLI principal for local synthetic repository studies."""
        return cls(
            principal_id="local-cli",
            allowed_projects=("*",),
            access_groups=("public", "synthetic", "default"),
            allow_restricted=False,
            allow_synthetic=True,
        )

    @classmethod
    def full_admin(cls, principal_id: str = "admin") -> AccessPrincipal:
        """Explicitly authorised project owner / test principal including restricted datasets."""
        return cls(
            principal_id=principal_id,
            allowed_projects=("*",),
            access_groups=("*",),
            allow_restricted=True,
            allow_synthetic=True,
        )

    def cache_scope(self) -> str:
        """Canonical access-scope digest included in every stage cache key (section 19.3)."""
        if (
            self.principal_id == "local-cli"
            and self.allowed_projects == ("*",)
            and self.access_groups == ("public", "synthetic", "default")
            and not self.allow_restricted
            and self.allow_synthetic
        ):
            return "local"
        digest = short_hash(
            content_hash(
                {
                    "projects": sorted(set(self.allowed_projects)),
                    "groups": sorted(set(self.access_groups)),
                    "restricted": self.allow_restricted,
                    "synthetic": self.allow_synthetic,
                }
            ),
            16,
        )
        return f"scope-{digest}"


def authorize_project(principal: AccessPrincipal, project_id: str) -> None:
    """Require project-level permission for ``project_id``."""
    if "*" in principal.allowed_projects:
        return
    if project_id not in principal.allowed_projects:
        raise AccessDeniedError("denied access: caller is not authorised for this project")


def can_access_dataset(principal: AccessPrincipal, dataset: Dataset, project_id: str) -> bool:
    """True when ``principal`` may access ``dataset`` within ``project_id``."""
    if "*" not in principal.allowed_projects and project_id not in principal.allowed_projects:
        return False
    if dataset.synthetic and not principal.allow_synthetic:
        return False
    if dataset.allowed_use is AllowedUse.PUBLIC:
        return True
    if dataset.allowed_use is AllowedUse.RESTRICTED and not principal.allow_restricted:
        return False
    if "*" in principal.access_groups:
        return True
    return dataset.access_group in principal.access_groups


def authorize_dataset(principal: AccessPrincipal, dataset: Dataset, project_id: str) -> None:
    """Enforce project, allowed_use, and access_group constraints on a dataset."""
    authorize_project(principal, project_id)
    if dataset.synthetic and not principal.allow_synthetic:
        raise AccessDeniedError("denied access: caller cannot access synthetic datasets")
    if dataset.allowed_use is AllowedUse.PUBLIC:
        return
    if dataset.allowed_use is AllowedUse.RESTRICTED and not principal.allow_restricted:
        raise AccessDeniedError("denied access: caller lacks restricted dataset permission")
    if "*" not in principal.access_groups and dataset.access_group not in principal.access_groups:
        raise AccessDeniedError("denied access: caller is outside the dataset access group")


def authorize_scenario(principal: AccessPrincipal, scenario: Scenario) -> None:
    """Verify access to the scenario's project, study synthetic mode, and every dataset."""
    authorize_project(principal, scenario.study.project_id)
    if scenario.study.synthetic and not principal.allow_synthetic:
        raise AccessDeniedError("denied access: synthetic studies are disabled for this caller")
    for dataset in scenario.datasets:
        authorize_dataset(principal, dataset, scenario.study.project_id)


def authorize_run(principal: AccessPrincipal, store: Store, run_id: str) -> Scenario:
    """Load and authorise the frozen scenario behind ``run_id`` before any run access."""
    run = store.get_run(run_id)
    scenario = store.get_scenario(run.scenario_revision)
    authorize_scenario(principal, scenario)
    return scenario


def authorize_job(principal: AccessPrincipal, store: Store, job: JobRecord) -> None:
    """Authorise access to a job record and its underlying frozen scenario revision."""
    authorize_project(principal, job.project_id)
    scenario = store.get_scenario(job.scenario_revision)
    authorize_scenario(principal, scenario)


def filter_accessible_datasets(
    principal: AccessPrincipal, scenario: Scenario
) -> tuple[Dataset, ...]:
    """Return only those datasets of ``scenario`` visible to ``principal``."""
    authorize_project(principal, scenario.study.project_id)
    return tuple(
        dataset
        for dataset in scenario.datasets
        if can_access_dataset(principal, dataset, scenario.study.project_id)
    )
