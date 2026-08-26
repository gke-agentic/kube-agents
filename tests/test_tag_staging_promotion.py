"""Unit tests for scripts/release/tag_staging_promotion.sh.

Tests argument validation, the staging/ namespace guard, local tag creation, and
idempotency against a tag that already exists.
"""

import pathlib
import subprocess
import unittest

from tests.testing.common import (
    MOCK_SAMPLE_COMMIT_SHA,
    create_mock_git_repo,
    get_isolated_test_env,
)
from tests.testing.release import (
    MOCK_LATEST_STAGING_TAG,
    MOCK_LATEST_VALIDATED_RC_TAG,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_TAG_SCRIPT = _REPO_ROOT / "scripts" / "release" / "tag_staging_promotion.sh"


class TagStagingPromotionTest(unittest.TestCase):
    def _run_script(self, repo_dir, args=None, env=None):
        return subprocess.run(
            ["bash", str(_TAG_SCRIPT)] + (args or []),
            capture_output=True,
            text=True,
            env=get_isolated_test_env(overrides=env),
            cwd=repo_dir,
        )

    def _tagged_sha(self, git, tag):
        return git("rev-parse", f"{tag}^{{commit}}").stdout.strip()

    def test_missing_arguments(self):
        temp_dir, repo_dir, _ = create_mock_git_repo()
        try:
            proc = self._run_script(repo_dir)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("COMMIT_SHA and STAGING_TAG are required", proc.stderr)

            proc = self._run_script(repo_dir, args=[MOCK_SAMPLE_COMMIT_SHA])
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("COMMIT_SHA and STAGING_TAG are required", proc.stderr)
        finally:
            temp_dir.cleanup()

    def test_tag_outside_the_staging_namespace_is_rejected(self):
        """staging-redeploy-*.yml triggers on staging/** and on nothing else."""
        temp_dir, repo_dir, git = create_mock_git_repo()
        try:
            head = git("rev-parse", "HEAD").stdout.strip()
            for bad_tag in ("rc_2608241820_b35543c", "staging-2026-08-26", "0.2.0"):
                with self.subTest(bad_tag=bad_tag):
                    proc = self._run_script(repo_dir, args=[head, bad_tag])
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertIn("must live under 'staging/'", proc.stderr)
        finally:
            temp_dir.cleanup()

    def test_creates_the_staging_tag_on_the_target_commit(self):
        temp_dir, repo_dir, git = create_mock_git_repo()
        try:
            head = git("rev-parse", "HEAD").stdout.strip()
            proc = self._run_script(
                repo_dir,
                args=[head, MOCK_LATEST_STAGING_TAG, MOCK_LATEST_VALIDATED_RC_TAG],
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(self._tagged_sha(git, MOCK_LATEST_STAGING_TAG), head)
        finally:
            temp_dir.cleanup()

    def test_local_run_does_not_push(self):
        """Outside CI the script stops at the local tag -- no remote is configured."""
        temp_dir, repo_dir, git = create_mock_git_repo()
        try:
            head = git("rev-parse", "HEAD").stdout.strip()
            proc = self._run_script(repo_dir, args=[head, MOCK_LATEST_STAGING_TAG])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Remote push skipped", proc.stdout)
        finally:
            temp_dir.cleanup()

    def test_reads_environment_variables(self):
        temp_dir, repo_dir, git = create_mock_git_repo()
        try:
            head = git("rev-parse", "HEAD").stdout.strip()
            proc = self._run_script(
                repo_dir,
                env={
                    "COMMIT_SHA": head,
                    "STAGING_TAG": MOCK_LATEST_STAGING_TAG,
                    "RC_TAG": MOCK_LATEST_VALIDATED_RC_TAG,
                },
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(self._tagged_sha(git, MOCK_LATEST_STAGING_TAG), head)
        finally:
            temp_dir.cleanup()

    def test_rerun_on_the_same_commit_is_idempotent(self):
        temp_dir, repo_dir, git = create_mock_git_repo()
        try:
            head = git("rev-parse", "HEAD").stdout.strip()
            first = self._run_script(repo_dir, args=[head, MOCK_LATEST_STAGING_TAG])
            self.assertEqual(first.returncode, 0, first.stderr)

            second = self._run_script(repo_dir, args=[head, MOCK_LATEST_STAGING_TAG])
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("Idempotent skip", second.stdout)
        finally:
            temp_dir.cleanup()

    def test_existing_tag_on_another_commit_fails(self):
        temp_dir, repo_dir, git = create_mock_git_repo()
        try:
            first_sha = git("rev-parse", "HEAD").stdout.strip()
            git("tag", "-a", MOCK_LATEST_STAGING_TAG, "-m", "Existing promotion")

            (pathlib.Path(repo_dir) / "second.txt").write_text("second commit\n")
            git("add", "second.txt")
            git("commit", "-m", "feat: second commit")
            second_sha = git("rev-parse", "HEAD").stdout.strip()

            proc = self._run_script(repo_dir, args=[second_sha, MOCK_LATEST_STAGING_TAG])
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("already exists but points to commit", proc.stderr)
            self.assertEqual(self._tagged_sha(git, MOCK_LATEST_STAGING_TAG), first_sha)
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
