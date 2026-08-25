"""Unit tests for scripts/release/execute_e2e_tests.py.

Covers the environment the runner hands its pytest child. The suite itself needs a live
GKE cluster, so the child is stubbed and only the environment is asserted.
"""

import importlib.util
import os
import pathlib
import unittest
from unittest import mock

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_RUNNER = _REPO_ROOT / "scripts" / "release" / "execute_e2e_tests.py"


def _load_runner():
    """Imports the runner by path -- scripts/release is not a package.

    GOOGLE_APPLICATION_CREDENTIALS is cleared first: the module shells out to
    `gcloud auth activate-service-account` at import time when it names a real file.
    """
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
        spec = importlib.util.spec_from_file_location("execute_e2e_tests", _RUNNER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class ChildEnvironmentTest(unittest.TestCase):
    """run_environment_tests must name the environment it selected in the child's env."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = _load_runner()

    def _run(self, env, ambient=None):
        """Calls run_environment_tests with the child stubbed, returning its environment."""
        captured = {}

        def fake_run(cmd, env=None, cwd=None):
            captured.update(env or {})
            return mock.Mock(returncode=0)

        base = {
            "GCP_PROJECT_ID": "test-project",
            "GKE_CLUSTER_NAME": "test-cluster",
            "GCP_REGION": "us-east4",
        }
        base.update(ambient or {})
        with mock.patch.dict(os.environ, base, clear=False), \
                mock.patch.object(self.runner, "subprocess") as sub, \
                mock.patch.object(self.runner, "connect_gke_credentials"), \
                mock.patch.object(self.runner, "find_pytest_executable", return_value="pytest"):
            sub.run.side_effect = fake_run
            self.runner.run_environment_tests(env, {"region": "us-east4"}, [])
        return captured

    def test_selected_environment_is_named_for_the_child(self) -> None:
        captured = self._run({"name": "audit-e2e", "tests": ["tests/e2e/x.py"]})
        self.assertEqual(captured.get("E2E_ENV"), "audit-e2e")

    def test_selection_overrides_a_conflicting_ambient_value(self) -> None:
        """The regression: E2E_ENV must be set after **os.environ, not before.

        `--env` and a stale exported E2E_ENV disagree, and the runner's choice is the
        one that ran. Reorder the dict so **os.environ lands last and `--env all` hands
        every child E2E_ENV=all, a name no environment in e2e_config.yaml has.
        """
        captured = self._run(
            {"name": "audit-e2e", "tests": ["tests/e2e/x.py"]},
            ambient={"E2E_ENV": "all"},
        )
        self.assertEqual(captured.get("E2E_ENV"), "audit-e2e")

    def test_environment_specific_env_vars_still_reach_the_child(self) -> None:
        captured = self._run(
            {
                "name": "audit-e2e",
                "tests": ["tests/e2e/x.py"],
                "env_vars": {"FLEET_AUDIT_STREAMS": "compliance-audit"},
            }
        )
        self.assertEqual(captured.get("FLEET_AUDIT_STREAMS"), "compliance-audit")
        self.assertEqual(captured.get("E2E_ENV"), "audit-e2e")


class ConfigDefaultTest(unittest.TestCase):
    """The fallback environment name has to be one e2e_config.yaml actually defines."""

    def test_default_environment_fallback_names_a_real_environment(self) -> None:
        import yaml

        config = yaml.safe_load((_REPO_ROOT / "tests" / "e2e" / "e2e_config.yaml").read_text())
        names = {e["name"] for e in config["environments"]}
        self.assertIn(config["defaults"]["default_environment"], names)

        # Both copies of the hard-coded fallback -- the runner's and conftest's -- are
        # unreachable while that key exists, and a hard error the day it does not.
        for path in (
            _RUNNER,
            _REPO_ROOT / "tests" / "e2e" / "conftest.py",
        ):
            text = path.read_text()
            self.assertIn('default_environment", "investigations-e2e"', text, path.name)


if __name__ == "__main__":
    unittest.main()
