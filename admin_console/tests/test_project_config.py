from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from admin_console.project_config import (
    build_project_candidates,
    is_valid_project_id,
    load_provisioned_target,
)


class ProjectConfigTest(unittest.TestCase):
    def test_loads_only_valid_deployment_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "vars.sh"
            state.write_text(
                "\n".join(
                    (
                        "export PROJECT_ID=test-project-01",
                        "export CLUSTER_NAME=test-cluster-01",
                        "export REGION=us-east4",
                        "export NAMESPACE=kubeagents-system",
                        "export API_KEY=must-not-be-read",
                    )
                ),
                encoding="utf-8",
            )

            target = load_provisioned_target(state)

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.project_id, "test-project-01")
        self.assertEqual(target.cluster_name, "test-cluster-01")
        self.assertEqual(target.location, "us-east4")
        self.assertEqual(target.namespace, "kubeagents-system")

    def test_rejects_shell_expression_in_project_value(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "vars.sh"
            state.write_text(
                "export PROJECT_ID=$(touch /tmp/portal-must-not-execute)\n",
                encoding="utf-8",
            )
            self.assertIsNone(load_provisioned_target(state))

    def test_loads_a_hand_authored_install_env(self):
        """install.env is a dotenv: bare `K=V`, no `export`.

        A pattern that required `export` read nothing out of it and returned
        None, which the portal treats as "no provisioned target" and quietly
        falls back to the query parameter -- so the regression showed up as the
        wrong cluster preselected, not as an error.
        """
        with tempfile.TemporaryDirectory() as directory:
            install_env = Path(directory) / "install.env"
            install_env.write_text(
                "\n".join(
                    (
                        "# kube-agents install configuration",
                        "PROJECT_ID=test-project-01",
                        "CLUSTER_NAME=test-cluster-01",
                        "REGION=us-east4",
                        "GEMINI_API_KEY=must-not-be-read",
                    )
                ),
                encoding="utf-8",
            )
            target = load_provisioned_target(Path(directory) / "absent.sh", install_env)

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.project_id, "test-project-01")
        self.assertEqual(target.cluster_name, "test-cluster-01")
        self.assertEqual(target.location, "us-east4")
        self.assertEqual(target.namespace, "kubeagents-system")

    def test_the_input_wins_over_the_legacy_state_file(self):
        """Both exist during the migration, and they can disagree. The
        installers load install.env last, so the portal must too or it offers a
        cluster the installer is not pointed at."""
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "vars.sh"
            state.write_text(
                "export PROJECT_ID=stale-project-01\n"
                "export CLUSTER_NAME=stale-cluster-01\n"
                "export REGION=us-central1\n"
                "export NAMESPACE=agents\n",
                encoding="utf-8",
            )
            install_env = Path(directory) / "install.env"
            install_env.write_text(
                "PROJECT_ID=test-project-01\nCLUSTER_NAME=test-cluster-01\n",
                encoding="utf-8",
            )
            target = load_provisioned_target(state, install_env)

        assert target is not None
        self.assertEqual(target.project_id, "test-project-01")
        self.assertEqual(target.cluster_name, "test-cluster-01")
        # Not overridden by the input, so the recorded value stands rather than
        # reverting to the default.
        self.assertEqual(target.location, "us-central1")
        self.assertEqual(target.namespace, "agents")

    def test_rejects_shell_expression_in_an_install_env_too(self):
        """The never-sourced guarantee covers both files."""
        with tempfile.TemporaryDirectory() as directory:
            install_env = Path(directory) / "install.env"
            install_env.write_text(
                "PROJECT_ID=$(touch /tmp/portal-must-not-execute)\n",
                encoding="utf-8",
            )
            self.assertIsNone(
                load_provisioned_target(Path(directory) / "absent.sh", install_env)
            )

    def test_candidates_are_valid_and_deduplicated(self):
        self.assertTrue(is_valid_project_id("test-project-01"))
        self.assertFalse(is_valid_project_id("Not A Project"))
        candidates = build_project_candidates(
            None,
            "test-project-01",
            "test-project-01",
        )
        self.assertEqual(
            [(item.project_id, item.source) for item in candidates],
            [("test-project-01", "active gcloud configuration")],
        )

    def test_candidates_distinguish_saved_and_url_projects(self):
        candidates = build_project_candidates(
            None,
            "active-project-01",
            "url-project-01",
            "saved-project-01",
        )

        self.assertEqual(
            [(item.project_id, item.source) for item in candidates],
            [
                ("active-project-01", "active gcloud configuration"),
                ("saved-project-01", "saved connection"),
                ("url-project-01", "URL selection"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
