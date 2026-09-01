"""Tests for package_release_bundle.sh verifying offline bundle and archive creation."""

import os
import pathlib
import subprocess
import tarfile
import tempfile
import unittest
import zipfile

from tests.testing.common import (
    INVALID_GA_RELEASE_TAGS,
    MOCK_DEFAULT_REGISTRY_PREFIX,
    create_minimal_tools_bin,
    get_isolated_test_env,
)
from tests.testing.release import (
    MOCK_RELEASE_BUNDLE_VERSION,
    MOCK_TARGET_RELEASE_VERSION,
    create_mock_helm_binary,
    create_mock_syft_binary,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PACKAGE_SCRIPT = _REPO_ROOT / "scripts" / "release" / "package_release_bundle.sh"


class PackageReleaseBundleTest(unittest.TestCase):
    def _run_script(self, args, env=None, bin_dir=None):
        full_env = get_isolated_test_env(overrides=env, bin_dir=bin_dir)
        return subprocess.run(
            ["bash", str(_PACKAGE_SCRIPT), *args],
            capture_output=True,
            text=True,
            env=full_env,
            cwd=str(_REPO_ROOT),
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

    def test_tar_missing_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = create_minimal_tools_bin(temp_dir, exclude=("tar",))
            proc = self._run_script(
                [MOCK_TARGET_RELEASE_VERSION],
                env={"PATH": str(bin_dir)},
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("'tar' CLI is required", proc.stderr)

    def test_zip_missing_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = create_minimal_tools_bin(temp_dir, exclude=("zip",))
            proc = self._run_script(
                [MOCK_TARGET_RELEASE_VERSION],
                env={"PATH": str(bin_dir)},
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("'zip' CLI is required", proc.stderr)

    def test_helm_missing_in_ci_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = create_minimal_tools_bin(temp_dir, exclude=("helm",))
            proc = self._run_script(
                [MOCK_TARGET_RELEASE_VERSION],
                env={"CI": "true", "PATH": str(bin_dir)},
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("'helm' CLI is mandatory in CI", proc.stderr)

    def test_unresolvable_commit_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = create_minimal_tools_bin(temp_dir)
            proc = self._run_script(
                ["99.99.99"],
                env={"PATH": str(bin_dir), "REGISTRY_PREFIX": MOCK_DEFAULT_REGISTRY_PREFIX},
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Cannot resolve valid Git commit for release tag '99.99.99'", proc.stderr)

    def test_invalid_target_commit_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = create_minimal_tools_bin(temp_dir)
            proc = self._run_script(
                [MOCK_TARGET_RELEASE_VERSION],
                env={
                    "TARGET_COMMIT": "not-a-valid-commit-hash",
                    "PATH": str(bin_dir),
                    "REGISTRY_PREFIX": MOCK_DEFAULT_REGISTRY_PREFIX,
                },
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Explicit TARGET_COMMIT 'not-a-valid-commit-hash' is not a valid commit", proc.stderr)

    def test_successful_bundle_packaging(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            bin_dir = create_minimal_tools_bin(temp_dir)
            dist_dir = temp_path / "dist"

            create_mock_helm_binary(bin_dir)
            create_mock_syft_binary(bin_dir)

            head_commit = subprocess.check_output(
                ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"], text=True
            ).strip()

            proc = self._run_script(
                [MOCK_RELEASE_BUNDLE_VERSION],
                env={
                    "PATH": str(bin_dir),
                    "DIST_DIR": str(dist_dir),
                    "CI": "true",
                    "REGISTRY_PREFIX": MOCK_DEFAULT_REGISTRY_PREFIX,
                    "TARGET_COMMIT": head_commit,
                },
            )
            self.assertEqual(proc.returncode, 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")

            # Verify presence of expected artifacts in dist_dir
            tarball_path = dist_dir / f"kube-agents-{MOCK_RELEASE_BUNDLE_VERSION}.tar.gz"
            zip_path = dist_dir / f"kube-agents-{MOCK_RELEASE_BUNDLE_VERSION}.zip"
            chart_path = dist_dir / f"kube-agents-{MOCK_RELEASE_BUNDLE_VERSION}.tgz"
            spdx_fs = dist_dir / f"kube-agents-{MOCK_RELEASE_BUNDLE_VERSION}.spdx.json"
            cdx_fs = dist_dir / f"kube-agents-{MOCK_RELEASE_BUNDLE_VERSION}.cdx.json"
            checksums_path = dist_dir / "checksums.txt"

            self.assertTrue(tarball_path.exists(), "Tarball should exist")
            self.assertTrue(zip_path.exists(), "Zip archive should exist")
            self.assertTrue(chart_path.exists(), "Helm chart package should exist")
            self.assertTrue(spdx_fs.exists(), "SPDX SBOM should exist")
            self.assertTrue(cdx_fs.exists(), "CycloneDX SBOM should exist")
            self.assertTrue(checksums_path.exists(), "checksums.txt should exist")

            # Verify contents of unpacked tarball
            unpack_dir = temp_path / "unpacked"
            unpack_dir.mkdir()
            kwargs = {}
            if hasattr(tarfile, "data_filter"):
                kwargs["filter"] = "data"
            with tarfile.open(tarball_path, "r:gz") as tar:
                tar.extractall(unpack_dir, **kwargs)

            bundle_root = unpack_dir / f"kube-agents-{MOCK_RELEASE_BUNDLE_VERSION}"
            self.assertTrue(bundle_root.exists(), "Unpacked bundle directory should exist")

            # Verify marker
            marker_file = bundle_root / ".release-bundle"
            self.assertTrue(marker_file.exists(), ".release-bundle marker should exist")
            marker_content = marker_file.read_text()
            self.assertIn("name=kube-agents", marker_content)
            self.assertIn(f"version={MOCK_RELEASE_BUNDLE_VERSION}", marker_content)
            self.assertIn(f"tag={MOCK_RELEASE_BUNDLE_VERSION}", marker_content)
            self.assertIn(f"commit={head_commit}", marker_content)

            # Verify BAKED_RELEASE_VERSION stamping
            stamped_install = (bundle_root / "install.sh").read_text()
            self.assertIn(f'BAKED_RELEASE_VERSION="{MOCK_RELEASE_BUNDLE_VERSION}"', stamped_install)

            # Verify terraform/ directory exists in bundle and preserves tfvars examples
            self.assertTrue((bundle_root / "terraform").is_dir(), "terraform/ should be in bundle")
            self.assertTrue((bundle_root / "images.json").is_file(), "images.json should be in bundle")
            self.assertTrue(
                (bundle_root / "terraform" / "examples" / "full-install" / "terraform.tfvars.example").is_file(),
                "terraform.tfvars.example should be preserved in bundle",
            )
            self.assertTrue(
                (bundle_root / "terraform" / "examples" / "ci-pool-minter" / "terraform.tfvars.example").is_file(),
                "ci-pool-minter terraform.tfvars.example should be preserved in bundle",
            )
            self.assertFalse(
                (bundle_root / "terraform" / "examples" / "full-install" / "terraform.tfvars").exists(),
                "terraform.tfvars should be sanitized from bundle",
            )

            # Verify checksums.txt covers all files and excludes itself
            checksums_content = checksums_path.read_text()
            self.assertIn(tarball_path.name, checksums_content)
            self.assertIn(zip_path.name, checksums_content)
            self.assertIn(chart_path.name, checksums_content)
            self.assertIn(spdx_fs.name, checksums_content)
            self.assertNotIn("checksums.txt", checksums_content)

    def test_idempotent_rerun(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            bin_dir = create_minimal_tools_bin(temp_dir)
            dist_dir = temp_path / "dist"

            create_mock_helm_binary(bin_dir)
            create_mock_syft_binary(bin_dir)

            head_commit = subprocess.check_output(
                ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"], text=True
            ).strip()

            env = {
                "PATH": str(bin_dir),
                "DIST_DIR": str(dist_dir),
                "CI": "true",
                "REGISTRY_PREFIX": MOCK_DEFAULT_REGISTRY_PREFIX,
                "TARGET_COMMIT": head_commit,
            }

            proc1 = self._run_script([MOCK_RELEASE_BUNDLE_VERSION], env=env)
            self.assertEqual(proc1.returncode, 0, proc1.stderr)
            chk1 = (dist_dir / "checksums.txt").read_text()

            proc2 = self._run_script([MOCK_RELEASE_BUNDLE_VERSION], env=env)
            self.assertEqual(proc2.returncode, 0, proc2.stderr)
            chk2 = (dist_dir / "checksums.txt").read_text()

            files1 = [line.split()[-1] for line in chk1.strip().splitlines()]
            files2 = [line.split()[-1] for line in chk2.strip().splitlines()]
            self.assertEqual(files1, files2, "Reruns should cover identical set of release artifacts")
            self.assertFalse((dist_dir / "checksums.tmp").exists(), "No temporary files should be left")

    def test_bundle_staged_from_commit_excludes_untracked_and_ignored_working_tree_files(self):
        """Verifies release bundle extracts strictly from git commit tree and ignores untracked files."""
        untracked_root_file = _REPO_ROOT / "test_untracked_leak.tmp"
        untracked_tf_file = _REPO_ROOT / "terraform" / "test_lifecycle_override.tf"
        untracked_root_file.write_text("LEAK")
        untracked_tf_file.write_text("LEAK")

        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                temp_path = pathlib.Path(temp_dir)
                bin_dir = create_minimal_tools_bin(temp_dir)
                dist_dir = temp_path / "dist"
                create_mock_helm_binary(bin_dir)
                create_mock_syft_binary(bin_dir)

                head_commit = subprocess.check_output(
                    ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"], text=True
                ).strip()

                proc = self._run_script(
                    [MOCK_RELEASE_BUNDLE_VERSION],
                    env={
                        "PATH": str(bin_dir),
                        "DIST_DIR": str(dist_dir),
                        "CI": "true",
                        "REGISTRY_PREFIX": MOCK_DEFAULT_REGISTRY_PREFIX,
                        "TARGET_COMMIT": head_commit,
                    },
                )
                self.assertEqual(proc.returncode, 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")

                tarball_path = dist_dir / f"kube-agents-{MOCK_RELEASE_BUNDLE_VERSION}.tar.gz"
                unpack_dir = temp_path / "unpacked"
                unpack_dir.mkdir()
                kwargs = {}
                if hasattr(tarfile, "data_filter"):
                    kwargs["filter"] = "data"
                with tarfile.open(tarball_path, "r:gz") as tar:
                    tar.extractall(unpack_dir, **kwargs)

                bundle_root = unpack_dir / f"kube-agents-{MOCK_RELEASE_BUNDLE_VERSION}"
                self.assertFalse((bundle_root / "test_untracked_leak.tmp").exists(), "Untracked file must NOT be in bundle")
                self.assertFalse((bundle_root / "terraform" / "test_lifecycle_override.tf").exists(), "Override must NOT be in bundle")
                self.assertTrue((bundle_root / "install.sh").exists(), "Tracked install.sh must be in bundle")
            finally:
                if untracked_root_file.exists():
                    untracked_root_file.unlink()
                if untracked_tf_file.exists():
                    untracked_tf_file.unlink()


if __name__ == "__main__":
    unittest.main()
