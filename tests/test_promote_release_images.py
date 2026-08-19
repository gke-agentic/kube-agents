"""Unit tests for scripts/release/promote_release_images.sh.

Tests argument validation, pure numeric SemVer enforcement, and image promotion
execution with mock Docker CLI fixtures.
"""

import os
import pathlib
import subprocess
import tempfile
import unittest

from tests.testing.common import get_isolated_test_env
from tests.testing.release import (
    INVALID_GA_RELEASE_TAGS,
    MOCK_REQUIRED_RELEASE_IMAGES,
    MOCK_SAMPLE_COMMIT_SHA,
    MOCK_TARGET_RELEASE_TAG,
    create_mock_docker_binary,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PROMOTE_RELEASE_IMAGES_SH = _REPO_ROOT / "scripts" / "release" / "promote_release_images.sh"


class PromoteReleaseImagesScriptTest(unittest.TestCase):
    def _run_script(self, args, env=None, bin_dir=None):
        full_env = get_isolated_test_env(overrides=env, bin_dir=bin_dir)
        return subprocess.run(
            ["bash", str(_PROMOTE_RELEASE_IMAGES_SH)] + args,
            capture_output=True,
            text=True,
            env=full_env,
            cwd=str(_REPO_ROOT),
        )

    def test_missing_arguments(self):
        proc = self._run_script([])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("RELEASE_COMMIT and RELEASE_VERSION are required", proc.stderr)

    def test_invalid_tag_format(self):
        for bad_tag in INVALID_GA_RELEASE_TAGS:
            with self.subTest(bad_tag=bad_tag):
                proc = self._run_script([MOCK_SAMPLE_COMMIT_SHA, bad_tag])
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("not a valid pure numeric SemVer", proc.stderr)

    def test_promote_execution(self):
        temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        try:
            bin_dir = pathlib.Path(temp_dir.name) / "bin"
            create_mock_docker_binary(bin_dir)

            proc = self._run_script(
                [MOCK_SAMPLE_COMMIT_SHA, MOCK_TARGET_RELEASE_TAG],
                bin_dir=str(bin_dir),
            )
            self.assertEqual(proc.returncode, 0)
            self.assertIn("PROMOTING RELEASE CONTAINER IMAGES", proc.stdout)
            for img in MOCK_REQUIRED_RELEASE_IMAGES:
                self.assertIn(f"Promoting {img}", proc.stdout)
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
