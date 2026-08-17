"""Unit tests for scripts/release/common.sh helper routines and registries.

Tests boolean parsing, version canonicalization, repository and registry prefix
resolution, and declarative release registries.
"""

import os
import pathlib
import subprocess
import unittest

from tests.testing.common import (
    FALSY_BOOLEAN_INPUTS,
    MOCK_CUSTOM_ORG,
    MOCK_CUSTOM_REGISTRY_PREFIX,
    MOCK_CUSTOM_REPO,
    MOCK_CUSTOM_TARGET_REPO,
    MOCK_DEFAULT_REGISTRY_PREFIX,
    MOCK_DEFAULT_RELEASE_REPO,
    TRUTHY_BOOLEAN_INPUTS,
)
from tests.testing.release import MOCK_REQUIRED_RELEASE_IMAGES

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_COMMON_SH = _REPO_ROOT / "scripts" / "release" / "common.sh"


class ReleaseCommonTest(unittest.TestCase):
    def _run_common_func(self, func_call, env=None):
        """Source common.sh and execute the given bash snippet."""
        setup = f"""
source "{_COMMON_SH}"
{func_call}
"""
        full_env = dict(os.environ)
        full_env.update(env or {})
        return subprocess.run(
            ["bash", "-c", setup],
            capture_output=True,
            text=True,
            env=full_env,
            cwd=str(_REPO_ROOT),
        )

    def test_is_truthy(self):
        for val in TRUTHY_BOOLEAN_INPUTS:
            with self.subTest(val=val):
                proc = self._run_common_func(f'is_truthy "{val}"')
                self.assertEqual(proc.returncode, 0, f"Expected '{val}' to be truthy")

        for val in FALSY_BOOLEAN_INPUTS:
            with self.subTest(val=val):
                proc = self._run_common_func(f'is_truthy "{val}"')
                self.assertNotEqual(proc.returncode, 0, f"Expected '{val}' to be falsy")

    def test_get_target_repo(self):
        # Default
        proc = self._run_common_func('get_target_repo', env={"GH_ORG": "", "GH_REPO": "", "GITHUB_REPOSITORY": ""})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), MOCK_DEFAULT_RELEASE_REPO)

        # Via GITHUB_REPOSITORY
        proc = self._run_common_func('get_target_repo', env={"GITHUB_REPOSITORY": MOCK_CUSTOM_TARGET_REPO})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), MOCK_CUSTOM_TARGET_REPO)

        # Via GH_ORG and GH_REPO
        proc = self._run_common_func('get_target_repo', env={"GH_ORG": MOCK_CUSTOM_ORG, "GH_REPO": MOCK_CUSTOM_REPO})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), f"{MOCK_CUSTOM_ORG}/{MOCK_CUSTOM_REPO}")

    def test_get_registry_prefix(self):
        # Default
        proc = self._run_common_func('get_registry_prefix', env={"REGISTRY_PREFIX": "", "GH_ORG": "", "GH_REPO": "", "GITHUB_REPOSITORY": ""})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), MOCK_DEFAULT_REGISTRY_PREFIX)

        # Explicit REGISTRY_PREFIX
        proc = self._run_common_func('get_registry_prefix', env={"REGISTRY_PREFIX": MOCK_CUSTOM_REGISTRY_PREFIX})
        self.assertEqual(proc.stdout.strip(), MOCK_CUSTOM_REGISTRY_PREFIX)

    def test_required_release_images_registry(self):
        cmd = 'echo "IMAGES=${REQUIRED_RELEASE_IMAGES[*]}"'
        proc = self._run_common_func(cmd)
        self.assertEqual(proc.returncode, 0)
        for img in MOCK_REQUIRED_RELEASE_IMAGES:
            self.assertIn(img, proc.stdout)


if __name__ == "__main__":
    unittest.main()
