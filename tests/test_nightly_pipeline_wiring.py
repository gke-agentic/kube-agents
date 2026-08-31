"""Invariants of the nightly pipeline that only the workflow YAML can carry.

Four of these are failures that would be silent in CI — a green run that did the
wrong thing — which is why they are pinned here rather than left to review:

  * a job pointed at `rc` instead of `nightly` tears down the RC environment,
  * a skipped promotion job skips the teardown that `needs` it and leaves a GKE
    cluster billing,
  * a hardcoded `rc-environment` concurrency group makes an unrelated workflow
    contend for the release pipeline's cluster,
  * a staging tag shape the redeploy trigger does not match promotes nothing and
    still reports success.
"""

import fnmatch
import pathlib
import subprocess
import unittest

import yaml

from tests.testing.common import get_isolated_test_env

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"
_NIGHTLY = _WORKFLOWS / "nightly-pipeline.yml"
_COMMON_SH = _REPO_ROOT / "scripts" / "release" / "common.sh"

_STAGING_REDEPLOYS = (
    "staging-redeploy-agent.yml",
    "staging-redeploy-controller.yml",
    "staging-redeploy-integrations.yml",
)


def _doc(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text())


class NightlyPipelineWiringTest(unittest.TestCase):
    def setUp(self):
        self.doc = _doc(_NIGHTLY)
        self.jobs = self.doc["jobs"]

    def test_it_lands_without_a_schedule(self):
        """Dispatch-only until it has been exercised by hand.

        A cron here would point an untested pipeline at a GCP project on the
        night it merges. Turning the schedule on is its own reviewable change;
        delete this test in that change.
        """
        self.assertNotIn("schedule", self.doc[True])
        self.assertIn("workflow_dispatch", self.doc[True])

    def test_every_called_workflow_targets_the_nightly_environment(self):
        called = {name: job for name, job in self.jobs.items() if "uses" in job}
        self.assertTrue(called, "the pipeline is supposed to call reusable workflows")
        for name, job in called.items():
            with self.subTest(job=name):
                self.assertEqual(job["with"]["github_environment"], "nightly")

    def test_the_resolve_job_binds_the_nightly_environment(self):
        """It reads vars.REGISTRY_PREFIX; unbound, that resolves to empty in silence."""
        self.assertEqual(self.jobs["step-1-resolve-candidate"].get("environment"), "nightly")

    def test_the_promotion_job_is_not_gated_on_skip_promotion(self):
        """Gating the job would skip the teardown that needs it.

        A skipped job skips its dependents, and step 5 depends on step 4, so a
        night whose candidate was already promoted would leave its cluster
        running. The condition belongs on the steps.
        """
        job = self.jobs["step-4-promote-to-staging"]
        self.assertNotIn("skip_promotion", job["if"])
        step_conditions = [step.get("if", "") for step in job["steps"]]
        self.assertTrue(
            any("skip_promotion" in cond for cond in step_conditions),
            "the skip has to be expressed on the steps instead",
        )

    def test_teardown_depends_on_every_earlier_job_and_keeps_the_success_gate(self):
        teardown = self.jobs["step-5-teardown-env"]
        self.assertEqual(
            set(teardown["needs"]),
            {
                "step-1-resolve-candidate",
                "step-2-deploy-env",
                "step-3-run-e2e-matrix",
                "step-4-promote-to-staging",
            },
        )
        self.assertNotIn(
            "always()",
            teardown["if"],
            "always() removes the implicit success() and destroys the environments "
            "a failed run leaves standing for diagnosis",
        )

    def test_the_promotion_tag_is_pushed_with_the_release_bot_token(self):
        """A tag pushed with GITHUB_TOKEN triggers no workflow, so staging never deploys."""
        checkout = next(
            step
            for step in self.jobs["step-4-promote-to-staging"]["steps"]
            if str(step.get("uses", "")).startswith("actions/checkout@")
        )
        self.assertIn("RELEASE_BOT_TOKEN", checkout["with"]["token"])


class ConcurrencyGroupTest(unittest.TestCase):
    def test_no_workflow_hardcodes_the_rc_environment_lock(self):
        """The lock follows the environment, so nothing contends for a cluster it does not deploy to."""
        for path in sorted(_WORKFLOWS.glob("*.yml")):
            with self.subTest(workflow=path.name):
                doc = _doc(path)
                groups = []
                top = doc.get("concurrency")
                if isinstance(top, dict):
                    groups.append(top.get("group"))
                for job in (doc.get("jobs") or {}).values():
                    job_conc = job.get("concurrency")
                    if isinstance(job_conc, dict):
                        groups.append(job_conc.get("group"))
                self.assertNotIn("rc-environment", groups)


class StagingTagContractTest(unittest.TestCase):
    """The tag the pipeline pushes has to match the tag staging deploys on."""

    def _derived_tag(self) -> str:
        proc = subprocess.run(
            ["bash", "-c", f'source "{_COMMON_SH}"; staging_tag_for_rc "rc_2608241820_b35543c_validated"'],
            capture_output=True,
            text=True,
            env=get_isolated_test_env(),
            cwd=str(_REPO_ROOT),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def test_the_derived_tag_matches_every_staging_redeploy_trigger(self):
        tag = self._derived_tag()
        for workflow in _STAGING_REDEPLOYS:
            with self.subTest(workflow=workflow):
                patterns = _doc(_WORKFLOWS / workflow)[True]["push"]["tags"]
                self.assertTrue(
                    any(fnmatch.fnmatch(tag, pattern) for pattern in patterns),
                    f"{tag!r} matches none of {patterns!r}",
                )


if __name__ == "__main__":
    unittest.main()
