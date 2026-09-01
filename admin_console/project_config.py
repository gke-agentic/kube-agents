"""Safe discovery and validation of admin-console deployment scope."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
CLUSTER_NAME_PATTERN = re.compile(r"^[a-z](?:[a-z0-9-]{0,38}[a-z0-9])?$")
LOCATION_PATTERN = re.compile(r"^[a-z]+-[a-z0-9]+[0-9](?:-[a-z])?$")
REGION_PATTERN = re.compile(r"^[a-z]+-[a-z0-9]+[0-9]$")
NAMESPACE_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$"
)
STATE_KEYS = {"PROJECT_ID", "CLUSTER_NAME", "REGION", "NAMESPACE"}
TARGET_SCOPE_HEADERS = (
    ("x-kube-agents-project", "project_id"),
    ("x-kube-agents-cluster", "cluster_name"),
    ("x-kube-agents-location", "location"),
    ("x-kube-agents-namespace", "namespace"),
)


@dataclass(frozen=True)
class DeploymentTarget:
    project_id: str
    cluster_name: str = ""
    location: str = ""
    namespace: str = "kubeagents-system"
    source: str = "provisioned state"


@dataclass(frozen=True)
class ProjectCandidate:
    project_id: str
    source: str


def deployment_target_headers(target: DeploymentTarget) -> dict[str, str]:
    """Return the request scope used to reject stale browser tabs."""

    return {
        header: str(getattr(target, attribute))
        for header, attribute in TARGET_SCOPE_HEADERS
    }


def is_valid_project_id(value: str) -> bool:
    """Return whether value is a syntactically valid Google Cloud project ID."""
    return bool(PROJECT_ID_PATTERN.fullmatch(value.strip()))


def is_valid_cluster_name(value: str) -> bool:
    return bool(CLUSTER_NAME_PATTERN.fullmatch(value.strip()))


def is_valid_location(value: str) -> bool:
    return bool(LOCATION_PATTERN.fullmatch(value.strip()))


def is_valid_region(value: str) -> bool:
    return bool(REGION_PATTERN.fullmatch(value.strip()))


def is_valid_namespace(value: str) -> bool:
    return bool(NAMESPACE_PATTERN.fullmatch(value.strip()))


def _parse_assignment_value(raw_value: str) -> str:
    """Parse shell quoting without evaluating substitutions or sourcing code."""
    try:
        words = shlex.split(raw_value, comments=False, posix=True)
    except ValueError:
        return ""
    return words[0] if len(words) == 1 else ""


# `export` is optional because the two files this reads differ: install.env is
# a hand-authored dotenv (`K=V`) and the vars.sh it replaced was generated with
# `printf %q` (`export K=V`). A pattern that required `export` matched nothing
# in install.env and returned None, which the portal reads as "no provisioned
# target" -- it falls back to the query parameter and the persisted connection
# rather than failing, so the regression would be silent.
_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?(PROJECT_ID|CLUSTER_NAME|REGION|NAMESPACE)=(.*)$"
)


def _read_assignments(path: Path) -> dict[str, str]:
    """The allowlisted assignments in one file, or {} if it cannot be read."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        match = _ASSIGNMENT.fullmatch(line)
        if not match or match.group(1) not in STATE_KEYS:
            continue
        values[match.group(1)] = _parse_assignment_value(match.group(2).strip())
    return values


def load_provisioned_target(
    vars_path: Path, install_env_path: Path | None = None
) -> DeploymentTarget | None:
    """Read the non-secret deployment coordinates allowlist from the install.

    Both files may contain secrets and both are shell-ish. Neither is ever
    sourced by the portal. Only fixed assignment names and validated values are
    accepted here.

    `install_env_path` is the hand-authored input and wins on every key it
    carries; `vars_path` is the generated state it replaced, still read so a
    deployment from before the change keeps working.
    """
    values = _read_assignments(vars_path)
    if install_env_path is not None:
        values.update(_read_assignments(install_env_path))
    if not values:
        return None

    project_id = values.get("PROJECT_ID", "")
    cluster_name = values.get("CLUSTER_NAME", "")
    location = values.get("REGION", "")
    namespace = values.get("NAMESPACE", "") or "kubeagents-system"
    if not is_valid_project_id(project_id):
        return None
    if cluster_name and not CLUSTER_NAME_PATTERN.fullmatch(cluster_name):
        cluster_name = ""
    if location and not LOCATION_PATTERN.fullmatch(location):
        location = ""
    if not NAMESPACE_PATTERN.fullmatch(namespace):
        namespace = "kubeagents-system"

    return DeploymentTarget(
        project_id=project_id,
        cluster_name=cluster_name,
        location=location,
        namespace=namespace,
    )


def build_project_candidates(
    provisioned: DeploymentTarget | None,
    configured_project: str,
    requested_project: str = "",
    persisted_project: str = "",
) -> tuple[ProjectCandidate, ...]:
    """Return unique, validated project choices in preferred order."""
    candidates: list[ProjectCandidate] = []

    def add(project_id: str, source: str) -> None:
        project_id = project_id.strip()
        if not is_valid_project_id(project_id):
            return
        if any(item.project_id == project_id for item in candidates):
            return
        candidates.append(ProjectCandidate(project_id, source))

    if provisioned:
        add(provisioned.project_id, provisioned.source)
    add(configured_project, "active gcloud configuration")
    add(persisted_project, "saved connection")
    add(requested_project, "URL selection")
    return tuple(candidates)
