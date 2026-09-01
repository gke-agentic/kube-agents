"""Unit tests for scripts/release/generate_release_sbom.sh.

Tests CLI arguments validation, syft presence checks in CI vs local,
SPDX 2.3 and CycloneDX 1.5 JSON filesystem SBOM generation, and OCI image SBOM generation.
"""

import json
import os
import pathlib
import subprocess
import tempfile
import unittest

from tests.testing.common import (
    INVALID_GA_RELEASE_TAGS,
    MOCK_DEFAULT_REGISTRY_PREFIX,
    create_minimal_tools_bin,
    get_isolated_test_env,
)
from tests.testing.release import (
    MOCK_RELEASE_BUNDLE_VERSION,
    MOCK_REQUIRED_RELEASE_IMAGES,
    MOCK_TARGET_RELEASE_VERSION,
    create_mock_syft_binary,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SBOM_SCRIPT = _REPO_ROOT / "scripts" / "release" / "generate_release_sbom.sh"


class GenerateReleaseSbomTest(unittest.TestCase):
    def _run_script(self, args=None, env=None, bin_dir=None, cwd=None):
        cmd = ["bash", str(_SBOM_SCRIPT)] + (args or [])
        full_env = get_isolated_test_env(overrides=env, bin_dir=bin_dir)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=full_env,
            cwd=cwd or str(_REPO_ROOT),
        )

    def test_missing_tag_fails(self):
        proc = self._run_script([])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("TAG_NAME must be specified", proc.stderr)

    def test_invalid_semver_fails(self):
        for bad_tag in INVALID_GA_RELEASE_TAGS:
            with self.subTest(bad_tag=bad_tag):
                proc = self._run_script([bad_tag])
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("not a valid pure numeric SemVer", proc.stderr)

    def test_nonexistent_target_dir_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = pathlib.Path(temp_dir) / "bin"
            create_mock_syft_binary(bin_dir)
            nonexistent = pathlib.Path(temp_dir) / "does-not-exist"

            proc = self._run_script([MOCK_TARGET_RELEASE_VERSION, str(nonexistent)], bin_dir=bin_dir)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Target directory", proc.stderr)

    def test_syft_missing_in_ci_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = create_minimal_tools_bin(temp_dir, exclude=("syft",))
            proc = self._run_script(
                [MOCK_TARGET_RELEASE_VERSION],
                env={"CI": "true", "PATH": str(bin_dir)},
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("syft' CLI is mandatory in CI", proc.stderr)

    def test_syft_missing_off_ci_warns_and_succeeds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = create_minimal_tools_bin(temp_dir, exclude=("syft",))
            proc = self._run_script(
                [MOCK_TARGET_RELEASE_VERSION],
                env={"CI": "", "GITHUB_ACTIONS": "", "PATH": str(bin_dir)},
            )
            self.assertEqual(proc.returncode, 0)
            self.assertIn("Skipping local SBOM generation", proc.stderr)

    def test_successful_sbom_generation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            bin_dir = temp_path / "bin"
            dist_dir = temp_path / "dist"
            target_dir = temp_path / "stage"
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "sample.txt").write_text("sample content")

            create_mock_syft_binary(bin_dir)

            proc = self._run_script(
                [MOCK_RELEASE_BUNDLE_VERSION, str(target_dir)],
                env={
                    "DIST_DIR": str(dist_dir),
                    "CI": "true",
                    "REGISTRY_PREFIX": MOCK_DEFAULT_REGISTRY_PREFIX,
                },
                bin_dir=bin_dir,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

            # Verify filesystem SBOM files
            spdx_fs = dist_dir / f"kube-agents-{MOCK_RELEASE_BUNDLE_VERSION}.spdx.json"
            cdx_fs = dist_dir / f"kube-agents-{MOCK_RELEASE_BUNDLE_VERSION}.cdx.json"
            self.assertTrue(spdx_fs.exists(), "SPDX filesystem SBOM should exist")
            self.assertTrue(cdx_fs.exists(), "CycloneDX filesystem SBOM should exist")

            spdx_data = json.loads(spdx_fs.read_text())
            self.assertEqual(spdx_data.get("spdxVersion"), "SPDX-2.3")

            cdx_data = json.loads(cdx_fs.read_text())
            self.assertEqual(cdx_data.get("bomFormat"), "CycloneDX")

            # Verify container image SBOM files
            for img in MOCK_REQUIRED_RELEASE_IMAGES:
                img_sbom = dist_dir / f"{img}-{MOCK_RELEASE_BUNDLE_VERSION}.spdx.json"
                self.assertTrue(img_sbom.exists(), f"Image SBOM for {img} should exist")
                img_data = json.loads(img_sbom.read_text())
                self.assertEqual(img_data.get("spdxVersion"), "SPDX-2.3")

    def test_image_sbom_failure_in_ci_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            bin_dir = temp_path / "bin"
            dist_dir = temp_path / "dist"
            target_dir = temp_path / "stage"
            target_dir.mkdir(parents=True, exist_ok=True)

            failing_img = MOCK_REQUIRED_RELEASE_IMAGES[0]
            create_mock_syft_binary(bin_dir, fail_on_images=[failing_img])

            proc = self._run_script(
                [MOCK_RELEASE_BUNDLE_VERSION, str(target_dir)],
                env={
                    "DIST_DIR": str(dist_dir),
                    "CI": "true",
                    "REGISTRY_PREFIX": MOCK_DEFAULT_REGISTRY_PREFIX,
                },
                bin_dir=bin_dir,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Failed to generate SBOM for container image", proc.stderr)

    def test_image_sbom_failure_off_ci_warns_and_continues(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            bin_dir = temp_path / "bin"
            dist_dir = temp_path / "dist"
            target_dir = temp_path / "stage"
            target_dir.mkdir(parents=True, exist_ok=True)

            failing_img = MOCK_REQUIRED_RELEASE_IMAGES[0]
            create_mock_syft_binary(bin_dir, fail_on_images=[failing_img])

            proc = self._run_script(
                [MOCK_RELEASE_BUNDLE_VERSION, str(target_dir)],
                env={
                    "DIST_DIR": str(dist_dir),
                    "CI": "",
                    "GITHUB_ACTIONS": "",
                    "REGISTRY_PREFIX": MOCK_DEFAULT_REGISTRY_PREFIX,
                },
                bin_dir=bin_dir,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Could not generate remote image SBOM", proc.stdout + proc.stderr)

    def test_image_failure_in_ci_leaves_dist_clean(self):
        """Verifies that an image generation failure in CI does not publish partial artifacts to dist_dir."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            bin_dir = temp_path / "bin"
            dist_dir = temp_path / "dist"
            target_dir = temp_path / "stage"
            target_dir.mkdir(parents=True, exist_ok=True)

            failing_img = MOCK_REQUIRED_RELEASE_IMAGES[0]
            create_mock_syft_binary(bin_dir, fail_on_images=[failing_img])

            proc = self._run_script(
                [MOCK_RELEASE_BUNDLE_VERSION, str(target_dir)],
                env={
                    "DIST_DIR": str(dist_dir),
                    "CI": "true",
                    "REGISTRY_PREFIX": MOCK_DEFAULT_REGISTRY_PREFIX,
                },
                bin_dir=bin_dir,
            )
            self.assertNotEqual(proc.returncode, 0)
            if dist_dir.exists():
                json_files = list(dist_dir.glob("*.json"))
                self.assertEqual(len(json_files), 0, f"Expected 0 files on failure, found: {json_files}")

    def test_idempotent_rerun(self):
        """Verifies running the script multiple times succeeds cleanly and produces identical valid artifacts."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            bin_dir = temp_path / "bin"
            dist_dir = temp_path / "dist"
            target_dir = temp_path / "stage"
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "app.txt").write_text(f"v{MOCK_RELEASE_BUNDLE_VERSION} content")

            create_mock_syft_binary(bin_dir)

            # First run
            proc1 = self._run_script(
                [MOCK_RELEASE_BUNDLE_VERSION, str(target_dir)],
                env={
                    "DIST_DIR": str(dist_dir),
                    "CI": "true",
                    "REGISTRY_PREFIX": MOCK_DEFAULT_REGISTRY_PREFIX,
                },
                bin_dir=bin_dir,
            )
            self.assertEqual(proc1.returncode, 0, proc1.stderr)
            files_run1 = sorted([f.name for f in dist_dir.glob("*.json")])
            self.assertGreater(len(files_run1), 0)

            # Second run (idempotent overwrite)
            proc2 = self._run_script(
                [MOCK_RELEASE_BUNDLE_VERSION, str(target_dir)],
                env={
                    "DIST_DIR": str(dist_dir),
                    "CI": "true",
                    "REGISTRY_PREFIX": MOCK_DEFAULT_REGISTRY_PREFIX,
                },
                bin_dir=bin_dir,
            )
            self.assertEqual(proc2.returncode, 0, proc2.stderr)
            files_run2 = sorted([f.name for f in dist_dir.glob("*.json")])
            self.assertEqual(files_run1, files_run2)


if __name__ == "__main__":
    unittest.main()
