"""Unit tests for scripts/release/install_pubsub_platform.sh and its callers.

The script was split out of wait_for_gke_readiness.sh so that installing alert
ingress is a step of its own with its own exit code. Two properties of that split
are invariants a later edit can break without any test noticing, so they are
pinned here: the ordering between the two scripts, and the fact that each caller
states its own failure policy rather than inheriting one from the script.
"""

import pathlib
import subprocess
import unittest

import yaml

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_INSTALL_SCRIPT = _REPO_ROOT / "scripts" / "release" / "install_pubsub_platform.sh"
_READINESS_SCRIPT = _REPO_ROOT / "scripts" / "release" / "wait_for_gke_readiness.sh"
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"

# Every workflow that waits for readiness against the RC cluster. Each one runs an
# alert-driven suite, so each one needs the adapter.
_CALLERS = (
    "rc-release-pipeline.yml",
    "e2e-nightly-matrix.yml",
    "e2e-manual-runner.yml",
)


def _steps(workflow_name: str) -> list[dict]:
    """Every step of every job in a workflow, in file order."""
    doc = yaml.safe_load((_WORKFLOWS / workflow_name).read_text())
    steps: list[dict] = []
    for job in doc.get("jobs", {}).values():
        steps.extend(job.get("steps", []) or [])
    return steps


def _run_index(steps: list[dict], needle: str) -> int:
    for i, step in enumerate(steps):
        if needle in (step.get("run") or ""):
            return i
    return -1


class TestInstallPubSubPlatformScript(unittest.TestCase):
    def test_script_exists_and_is_executable(self) -> None:
        self.assertTrue(_INSTALL_SCRIPT.is_file(), f"{_INSTALL_SCRIPT} is missing")
        self.assertTrue(
            _INSTALL_SCRIPT.stat().st_mode & 0o111,
            "install_pubsub_platform.sh must be executable; the workflows invoke it directly",
        )

    def test_script_parses(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(_INSTALL_SCRIPT)], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_readiness_script_no_longer_installs(self) -> None:
        """The split is the point: a script named wait_* must not mutate the cluster."""
        body = _READINESS_SCRIPT.read_text()
        self.assertNotIn(
            "agentplugins/pubsub-platform/install.sh",
            body,
            "wait_for_gke_readiness.sh installs the adapter again; that belongs in "
            "install_pubsub_platform.sh",
        )
        self.assertNotIn(
            "helm ",
            body,
            "wait_for_gke_readiness.sh should wait, not install",
        )

    def test_settle_loop_has_an_absolute_ceiling(self) -> None:
        """The stability window resets on every change, so it needs a hard bound.

        Without one, a gateway whose generation keeps moving spends the job's whole
        timeout-minutes budget and then fails for a reason that looks nothing like
        the cause.
        """
        body = _INSTALL_SCRIPT.read_text()
        self.assertIn("GENERATION_SETTLE_TIMEOUT", body)
        self.assertIn("settle_hard_deadline", body)


class TestCallerWiring(unittest.TestCase):
    def test_every_caller_installs_before_waiting(self) -> None:
        for workflow in _CALLERS:
            with self.subTest(workflow=workflow):
                steps = _steps(workflow)
                install_at = _run_index(steps, "install_pubsub_platform.sh")
                readiness_at = _run_index(steps, "wait_for_gke_readiness.sh")
                self.assertNotEqual(
                    install_at, -1, f"{workflow} never installs the Pub/Sub adapter"
                )
                self.assertNotEqual(
                    readiness_at, -1, f"{workflow} never waits for readiness"
                )
                self.assertLess(
                    install_at,
                    readiness_at,
                    f"{workflow} waits for readiness before installing the adapter, so the "
                    "rollout waits can pass against the pre-adapter ReplicaSet and the "
                    "gateway then restarts mid-suite",
                )

    def test_rc_pipeline_tolerates_an_ingress_failure(self) -> None:
        """Alert ingress feeds the optional suite only; it must not sink the Chat gate."""
        steps = _steps("rc-release-pipeline.yml")
        install_at = _run_index(steps, "install_pubsub_platform.sh")
        self.assertNotEqual(install_at, -1)
        self.assertIs(
            steps[install_at].get("continue-on-error"),
            True,
            "the RC pipeline's adapter step must be continue-on-error: a stockout-only "
            "problem must not fail the mandatory Google Chat gate",
        )

    def test_nightly_matrix_does_not_tolerate_an_ingress_failure(self) -> None:
        """The matrix exists to run alert-driven suites; dead ingress is a stop."""
        steps = _steps("e2e-nightly-matrix.yml")
        install_at = _run_index(steps, "install_pubsub_platform.sh")
        self.assertNotEqual(install_at, -1)
        self.assertIsNot(steps[install_at].get("continue-on-error"), True)


if __name__ == "__main__":
    unittest.main()
