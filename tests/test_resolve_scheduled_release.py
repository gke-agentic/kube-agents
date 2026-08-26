"""Unit tests for scripts/release/resolve_scheduled_release.sh.

Covers the four conditions an unattended release has to satisfy — a candidate
that passed the staging gate, something new to ship, a cycle that has not
already released, and no breaking change in the range — plus the outputs the
publish job reads. Every failing condition must be a skip with exit 0: a red
nightly run is the failure mode this gate exists to avoid.
"""

import datetime
import os
import pathlib
import subprocess
import unittest

from tests.testing.common import (
    create_mock_git_repo,
    get_isolated_test_env,
)
from tests.testing.release import (
    MOCK_COMMIT_MSG_BREAKING_BODY,
    MOCK_COMMIT_MSG_BREAKING_PRE_1_0,
    MOCK_COMMIT_MSG_FEAT,
    MOCK_HAND_PUSHED_STAGING_TAG,
    MOCK_LATEST_STAGING_TAG,
    MOCK_LATEST_VALIDATED_RC_TAG,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "release" / "resolve_scheduled_release.sh"

# 2026-08-28 is a Friday, so the anchor for any moment that day is its own
# midnight. The Wednesday before it is the last anchor of the previous cycle.
_FRIDAY_NOON = int(datetime.datetime(2026, 8, 28, 12, 0, tzinfo=datetime.timezone.utc).timestamp())
_FRIDAY_EARLY = int(datetime.datetime(2026, 8, 28, 6, 0, tzinfo=datetime.timezone.utc).timestamp())
_WEDNESDAY = int(datetime.datetime(2026, 8, 26, 9, 0, tzinfo=datetime.timezone.utc).timestamp())
_PREV_WEDNESDAY = int(datetime.datetime(2026, 8, 19, 9, 0, tzinfo=datetime.timezone.utc).timestamp())
_GA_TAG = "0.1.0"


class ResolveScheduledReleaseTest(unittest.TestCase):
    def _run(self, repo_dir, now_epoch=_FRIDAY_NOON, env=None):
        output_file = pathlib.Path(repo_dir) / "github_output.txt"
        output_file.write_text("")
        summary_file = pathlib.Path(repo_dir) / "step_summary.md"
        summary_file.write_text("")
        overrides = {
            "GITHUB_OUTPUT": str(output_file),
            "GITHUB_STEP_SUMMARY": str(summary_file),
            "NOW_EPOCH": str(now_epoch),
        }
        if env:
            overrides.update(env)

        proc = subprocess.run(
            ["bash", str(_SCRIPT)],
            capture_output=True,
            text=True,
            env=get_isolated_test_env(overrides=overrides),
            cwd=repo_dir,
        )

        outputs = {}
        for line in output_file.read_text().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                outputs[key] = value
        return proc, outputs, summary_file.read_text()

    @staticmethod
    def _tag_at(repo_dir, name, when_epoch):
        """Creates an annotated tag with a controlled creatordate."""
        env = os.environ.copy()
        env["GIT_COMMITTER_DATE"] = f"{when_epoch} +0000"
        subprocess.run(
            ["git", "tag", "-a", name, "-m", f"tag {name}"],
            cwd=repo_dir,
            check=True,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _repo(self, ga_tag_epoch=_PREV_WEDNESDAY, new_commit_msg=MOCK_COMMIT_MSG_FEAT, staging=True):
        """GA tag on the first commit, a second commit, staging tag on the second."""
        temp_dir, repo_dir, git = create_mock_git_repo()
        if ga_tag_epoch is not None:
            self._tag_at(repo_dir, _GA_TAG, ga_tag_epoch)

        (pathlib.Path(repo_dir) / "second.txt").write_text("second\n")
        git("add", "second.txt")
        git("commit", "-m", new_commit_msg)
        head = git("rev-parse", "HEAD").stdout.strip()

        if staging:
            git("tag", "-a", MOCK_LATEST_VALIDATED_RC_TAG, "-m", "validated candidate")
            git("tag", "-a", MOCK_LATEST_STAGING_TAG, "-m", "staging promotion")
        return temp_dir, repo_dir, git, head

    def test_no_staging_tag_is_a_skip_not_an_error(self):
        temp_dir, repo_dir, _, _ = self._repo(staging=False)
        try:
            proc, outputs, summary = self._run(repo_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["should_release"], "false")
            self.assertIn("staging gate", outputs["skip_reason"])
            self.assertIn("No release tonight", summary)
        finally:
            temp_dir.cleanup()

    def test_green_gate_in_a_due_cycle_releases(self):
        temp_dir, repo_dir, _, head = self._repo()
        try:
            proc, outputs, summary = self._run(repo_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["should_release"], "true")
            self.assertEqual(outputs["release_commit"], head)
            self.assertEqual(outputs["staging_tag"], MOCK_LATEST_STAGING_TAG)
            self.assertEqual(outputs["skip_reason"], "")
            self.assertIn("Releasing", summary)
        finally:
            temp_dir.cleanup()

    def test_first_ever_release_needs_no_previous_ga_tag(self):
        temp_dir, repo_dir, _, head = self._repo(ga_tag_epoch=None)
        try:
            proc, outputs, _ = self._run(repo_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["should_release"], "true")
            self.assertEqual(outputs["release_commit"], head)
        finally:
            temp_dir.cleanup()

    def test_nothing_new_since_the_last_ga_tag_is_a_skip(self):
        """GA tag and staging tag on the same commit — the ordinary quiet week."""
        temp_dir, repo_dir, git = create_mock_git_repo()
        try:
            self._tag_at(repo_dir, _GA_TAG, _PREV_WEDNESDAY)
            git("tag", "-a", MOCK_LATEST_VALIDATED_RC_TAG, "-m", "validated candidate")
            git("tag", "-a", MOCK_LATEST_STAGING_TAG, "-m", "staging promotion")
            proc, outputs, _ = self._run(repo_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["should_release"], "false")
            self.assertIn("No commits between", outputs["skip_reason"])
        finally:
            temp_dir.cleanup()

    def test_a_ga_tag_ahead_of_the_staging_commit_is_a_skip_not_a_collision(self):
        """The emergency-release shape: GA tagged on a commit newer than the gate's."""
        temp_dir, repo_dir, git = create_mock_git_repo()
        try:
            git("tag", "-a", MOCK_LATEST_VALIDATED_RC_TAG, "-m", "validated candidate")
            git("tag", "-a", MOCK_LATEST_STAGING_TAG, "-m", "staging promotion")
            (pathlib.Path(repo_dir) / "hotfix.txt").write_text("hotfix\n")
            git("add", "hotfix.txt")
            git("commit", "-m", "fix: emergency hotfix")
            self._tag_at(repo_dir, _GA_TAG, _PREV_WEDNESDAY)

            proc, outputs, _ = self._run(repo_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["should_release"], "false")
            self.assertIn("No commits between", outputs["skip_reason"])
        finally:
            temp_dir.cleanup()

    def test_a_release_already_made_this_cycle_is_a_skip(self):
        """GA tag created after this cycle's Friday anchor."""
        temp_dir, repo_dir, _, _ = self._repo(ga_tag_epoch=_FRIDAY_EARLY)
        try:
            proc, outputs, _ = self._run(repo_dir, now_epoch=_FRIDAY_NOON)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["should_release"], "false")
            self.assertIn("already released this cycle", outputs["skip_reason"])
        finally:
            temp_dir.cleanup()

    def test_a_blocked_cycle_retries_on_a_later_night(self):
        """Released last Wednesday, now the following Wednesday — still due.

        This is the retry the nightly cron exists for: the cycle's Friday came
        and went without a release, so a mid-week night still ships.
        """
        temp_dir, repo_dir, _, head = self._repo(ga_tag_epoch=_PREV_WEDNESDAY)
        try:
            proc, outputs, _ = self._run(repo_dir, now_epoch=_WEDNESDAY)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["should_release"], "true")
            self.assertEqual(outputs["release_commit"], head)
        finally:
            temp_dir.cleanup()

    def test_a_hand_pushed_staging_tag_does_not_become_the_release_target(self):
        """`staging/**` is the general deploy trigger, not the gate's namespace.

        A letter-initial hand tag sorts above every `staging/rc_*`, so without a
        shape filter it makes itself the newest "gated" candidate — and points
        the release at a commit the E2E matrix never saw.
        """
        temp_dir, repo_dir, git, gated_head = self._repo()
        try:
            # A side commit nobody validated, tagged the way a human deploys.
            git("checkout", "-q", "-b", "side", "HEAD~1")
            (pathlib.Path(repo_dir) / "side.txt").write_text("side\n")
            git("add", "side.txt")
            git("commit", "-m", "chore: something never gated")
            git("tag", "-a", MOCK_HAND_PUSHED_STAGING_TAG, "-m", "hand deploy")
            git("checkout", "-q", "main")

            proc, outputs, _ = self._run(repo_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["staging_tag"], MOCK_LATEST_STAGING_TAG)
            self.assertEqual(outputs["release_commit"], gated_head)
            self.assertEqual(outputs["should_release"], "true")
        finally:
            temp_dir.cleanup()

    def test_a_promotion_shaped_tag_on_an_unvalidated_commit_is_a_skip(self):
        """Not a red run: verify_release_eligibility.sh would exit 1 on this."""
        temp_dir, repo_dir, git = create_mock_git_repo()
        try:
            self._tag_at(repo_dir, _GA_TAG, _PREV_WEDNESDAY)
            (pathlib.Path(repo_dir) / "second.txt").write_text("second\n")
            git("add", "second.txt")
            git("commit", "-m", MOCK_COMMIT_MSG_FEAT)
            # Promotion-shaped, but no *_validated tag on the commit.
            git("tag", "-a", MOCK_LATEST_STAGING_TAG, "-m", "staging promotion")

            proc, outputs, _ = self._run(repo_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["should_release"], "false")
            self.assertIn("no *_validated tag", outputs["skip_reason"])
        finally:
            temp_dir.cleanup()

    def test_breaking_change_in_the_subject_halts_for_a_human(self):
        temp_dir, repo_dir, _, _ = self._repo(new_commit_msg=MOCK_COMMIT_MSG_BREAKING_PRE_1_0)
        try:
            proc, outputs, summary = self._run(repo_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["should_release"], "false")
            self.assertIn("breaking change", outputs["skip_reason"].lower())
            self.assertIn("published by a human", outputs["skip_reason"])
            self.assertIn("breaking change", summary.lower())
        finally:
            temp_dir.cleanup()

    def test_breaking_change_in_the_body_halts_for_a_human(self):
        temp_dir, repo_dir, _, _ = self._repo(new_commit_msg=MOCK_COMMIT_MSG_BREAKING_BODY)
        try:
            proc, outputs, _ = self._run(repo_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["should_release"], "false")
            self.assertIn("breaking change", outputs["skip_reason"].lower())
        finally:
            temp_dir.cleanup()

    def test_the_anchor_day_is_configurable(self):
        """Same repository, anchored to Monday instead: Friday is then mid-cycle.

        Released on the Wednesday before, which is *after* the Monday anchor of
        the cycle Friday belongs to, so nothing is due.
        """
        temp_dir, repo_dir, _, _ = self._repo(ga_tag_epoch=_WEDNESDAY)
        try:
            proc, outputs, _ = self._run(
                repo_dir, now_epoch=_FRIDAY_NOON, env={"RELEASE_ANCHOR_DOW": "1"}
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["should_release"], "false")
            self.assertIn("already released this cycle", outputs["skip_reason"])
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
