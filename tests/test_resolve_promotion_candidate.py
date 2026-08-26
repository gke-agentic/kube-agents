"""Unit tests for scripts/release/resolve_promotion_candidate.sh.

Tests candidate selection from validated RC tags, the validated-only gate on a
manually supplied tag, idempotency against an already-promoted commit, and the
step outputs the staging promotion pipeline reads.
"""

import pathlib
import subprocess
import unittest

from tests.testing.common import (
    MOCK_NONEXISTENT_REF,
    create_mock_git_repo,
    get_isolated_test_env,
)
from tests.testing.release import (
    MOCK_LATEST_STAGING_TAG,
    MOCK_LATEST_VALIDATED_RC_TAG,
    MOCK_OLDER_VALIDATED_RC_TAG,
    MOCK_UNVALIDATED_RC_TAG,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_RESOLVE_SCRIPT = _REPO_ROOT / "scripts" / "release" / "resolve_promotion_candidate.sh"


class ResolvePromotionCandidateTest(unittest.TestCase):
    def _run_script(self, repo_dir, args=None, env=None):
        """Runs the resolver with GITHUB_OUTPUT captured, and returns (proc, outputs)."""
        output_file = pathlib.Path(repo_dir) / "github_output.txt"
        output_file.write_text("")
        overrides = {"GITHUB_OUTPUT": str(output_file)}
        if env:
            overrides.update(env)

        proc = subprocess.run(
            ["bash", str(_RESOLVE_SCRIPT)] + (args or []),
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
        return proc, outputs

    def _repo_with_two_candidates(self):
        """Two validated candidates, older first, so 'latest' is a real choice."""
        temp_dir, repo_dir, git = create_mock_git_repo()

        git("tag", "-a", MOCK_OLDER_VALIDATED_RC_TAG, "-m", "Older validated RC")
        older_sha = git("rev-parse", "HEAD").stdout.strip()

        (pathlib.Path(repo_dir) / "second.txt").write_text("second commit\n")
        git("add", "second.txt")
        git("commit", "-m", "feat: second commit")
        git("tag", "-a", MOCK_LATEST_VALIDATED_RC_TAG, "-m", "Latest validated RC")
        latest_sha = git("rev-parse", "HEAD").stdout.strip()

        return temp_dir, repo_dir, git, older_sha, latest_sha

    def test_no_validated_candidate_fails(self):
        temp_dir, repo_dir, _ = create_mock_git_repo()
        try:
            proc, outputs = self._run_script(repo_dir)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("No validated release candidate tag", proc.stderr)
            self.assertEqual(outputs, {})
        finally:
            temp_dir.cleanup()

    def test_resolves_latest_validated_candidate(self):
        temp_dir, repo_dir, _, older_sha, latest_sha = self._repo_with_two_candidates()
        try:
            proc, outputs = self._run_script(repo_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["rc_tag"], MOCK_LATEST_VALIDATED_RC_TAG)
            self.assertEqual(outputs["commit_sha"], latest_sha)
            self.assertNotEqual(outputs["commit_sha"], older_sha)
            self.assertEqual(outputs["staging_tag"], MOCK_LATEST_STAGING_TAG)
            self.assertEqual(outputs["skip_promotion"], "false")
        finally:
            temp_dir.cleanup()

    def test_explicit_tag_overrides_the_latest(self):
        temp_dir, repo_dir, _, older_sha, _ = self._repo_with_two_candidates()
        try:
            proc, outputs = self._run_script(repo_dir, env={"RC_TAG": MOCK_OLDER_VALIDATED_RC_TAG})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["rc_tag"], MOCK_OLDER_VALIDATED_RC_TAG)
            self.assertEqual(outputs["commit_sha"], older_sha)
            self.assertEqual(outputs["skip_promotion"], "false")
        finally:
            temp_dir.cleanup()

    def test_positional_argument_is_accepted(self):
        temp_dir, repo_dir, _, older_sha, _ = self._repo_with_two_candidates()
        try:
            proc, outputs = self._run_script(repo_dir, args=[MOCK_OLDER_VALIDATED_RC_TAG])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["commit_sha"], older_sha)
        finally:
            temp_dir.cleanup()

    def test_unvalidated_candidate_is_rejected(self):
        """The gate is on the commit, not on the tag's spelling."""
        temp_dir, repo_dir, git = create_mock_git_repo()
        try:
            git("tag", "-a", MOCK_UNVALIDATED_RC_TAG, "-m", "Unvalidated RC")
            proc, outputs = self._run_script(repo_dir, env={"RC_TAG": MOCK_UNVALIDATED_RC_TAG})
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("carries no *_validated tag", proc.stderr)
            self.assertEqual(outputs, {})
        finally:
            temp_dir.cleanup()

    def test_unresolvable_tag_fails(self):
        temp_dir, repo_dir, _, _, _ = self._repo_with_two_candidates()
        try:
            proc, _ = self._run_script(repo_dir, env={"RC_TAG": MOCK_NONEXISTENT_REF})
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Cannot resolve valid Git commit SHA", proc.stderr)
        finally:
            temp_dir.cleanup()

    def test_already_promoted_commit_skips(self):
        temp_dir, repo_dir, git, _, latest_sha = self._repo_with_two_candidates()
        try:
            git("tag", "-a", MOCK_LATEST_STAGING_TAG, "-m", "Already promoted")
            proc, outputs = self._run_script(repo_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["skip_promotion"], "true")
            self.assertEqual(outputs["commit_sha"], latest_sha)
            self.assertIn("already promoted to staging", proc.stderr)
        finally:
            temp_dir.cleanup()

    def test_promotion_of_a_different_commit_does_not_skip(self):
        """A staging tag on the previous candidate must not block the new one."""
        temp_dir, repo_dir, git, older_sha, latest_sha = self._repo_with_two_candidates()
        try:
            git("tag", "-a", "staging/rc_2608181000_1111111", older_sha, "-m", "Previous promotion")
            proc, outputs = self._run_script(repo_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(outputs["commit_sha"], latest_sha)
            self.assertEqual(outputs["skip_promotion"], "false")
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
