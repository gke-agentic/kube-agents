"""Tests for the wiring in .github/workflows/release-publish.yml.

The gate that decides whether an unattended night releases lives in a shell
script with its own suite; what this file covers is the YAML that connects the
script's verdict to the publishing job, because that connection is where the
gate can be silently removed without any test going red.

Three expressions carry it, and each fails quietly if it is dropped:

  - `needs.evaluate-schedule.outputs.should_release == 'true'` on the publish
    job. Lose it and every night publishes.
  - the `outputs:` mapping on the gate job. Lose it and `should_release` reads
    as empty, which is falsy, so nothing ever publishes -- the harmless
    direction, but still silent.
  - the `TARGET_COMMIT` fallback to the gate's `release_commit`. Lose it and
    the scheduled run still publishes, just from whatever
    calculate_next_version.sh auto-resolves -- the newest rc_*_validated
    commit, which is precisely the commit the staging gate exists to stop us
    releasing. The gate becomes decorative and nothing looks wrong.

The file is also being rewritten by other in-flight work, so a conflict
resolution that loses one of these is a live risk rather than a hypothetical.
"""

import pathlib
import unittest

import yaml

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "release-publish.yml"

_GATE_JOB = "evaluate-schedule"
_PUBLISH_JOB = "publish-release"
_FORK_GUARD = "github.repository == 'gke-labs/kube-agents'"


def _load():
    # PyYAML resolves a bare `on:` key to the boolean True (YAML 1.1), which is
    # why this is not spelled workflow["on"].
    return yaml.safe_load(_WORKFLOW.read_text())


class ReleasePublishWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.workflow = _load()
        self.jobs = self.workflow["jobs"]
        self.triggers = self.workflow[True]

    def test_runs_on_a_nightly_schedule(self):
        crons = [entry["cron"] for entry in self.triggers["schedule"]]
        self.assertEqual(crons, ["17 1 * * *"])

    def test_the_nightly_attempt_precedes_the_staging_promotion(self):
        """01:17 has to be before staging-promote's 02:00, or it races the matrix."""
        promote = yaml.safe_load((_REPO_ROOT / ".github" / "workflows" / "staging-promote.yml").read_text())
        promote_crons = [entry["cron"] for entry in promote[True]["schedule"]]
        release_minutes = [self._cron_minutes(c) for c in (e["cron"] for e in self.triggers["schedule"])]
        promote_minutes = [self._cron_minutes(c) for c in promote_crons]
        self.assertTrue(
            max(release_minutes) < min(promote_minutes),
            f"release attempt {release_minutes} must precede staging promotion {promote_minutes}",
        )

    @staticmethod
    def _cron_minutes(expression):
        minute, hour = expression.split()[:2]
        return int(hour) * 60 + int(minute)

    def test_workflow_dispatch_is_still_available(self):
        self.assertIn("workflow_dispatch", self.triggers)

    def test_both_jobs_carry_the_fork_guard(self):
        """AGENTS.md requires it on every job of a self-triggering credentialed workflow."""
        for name in (_GATE_JOB, _PUBLISH_JOB):
            self.assertIn(_FORK_GUARD, self.jobs[name]["if"], f"{name} is missing the fork guard")

    def test_the_gate_job_exposes_the_outputs_the_publish_job_reads(self):
        outputs = self.jobs[_GATE_JOB]["outputs"]
        self.assertIn("should_release", outputs)
        self.assertIn("release_commit", outputs)

    def test_publishing_is_gated_on_the_verdict(self):
        publish = self.jobs[_PUBLISH_JOB]
        needs = publish["needs"]
        self.assertIn(_GATE_JOB, [needs] if isinstance(needs, str) else needs)
        condition = " ".join(publish["if"].split())
        self.assertIn(f"needs.{_GATE_JOB}.outputs.should_release == 'true'", condition)

    def test_a_scheduled_run_targets_the_staging_gated_commit(self):
        """Without the fallback the gate picks a commit the publish job ignores."""
        step = self._step(_PUBLISH_JOB, "Calculate Next Release Version")
        target = " ".join(step["env"]["TARGET_COMMIT"].split())
        self.assertIn(f"needs.{_GATE_JOB}.outputs.release_commit", target)
        self.assertIn("inputs.target_commit", target)

    def test_the_gate_job_runs_the_resolver(self):
        step = self._step(_GATE_JOB, "Decide")
        self.assertIn("resolve_scheduled_release.sh", step["run"])

    def test_the_gate_job_holds_no_write_permissions(self):
        """It only reads tags; the publish job is where write access belongs."""
        self.assertNotIn("permissions", self.jobs[_GATE_JOB])
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})

    def _step(self, job, name):
        for step in self.jobs[job]["steps"]:
            if step.get("name") == name:
                return step
        self.fail(f"step {name!r} not found in job {job!r}")


if __name__ == "__main__":
    unittest.main()
