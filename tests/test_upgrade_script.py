"""Unit tests for upgrade.sh validation and execution routines.

Tests pure numeric SemVer (X.Y.Z) references, 40-character commit SHAs,
piped stdin execution, and source ref alignment in upgrade.sh.
"""

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
import unittest

from tests.testing.common import (
    INVALID_IMMUTABLE_REFS,
    UPGRADER_HELP_BANNER,
    VALID_IMMUTABLE_REFS,
    get_isolated_test_env,
)
from tests.testing.release import (
    MOCK_RELEASE_BUNDLE_VERSION,
    create_mock_release_bundle_marker,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_UPGRADE_SH = _REPO_ROOT / "upgrade.sh"


class UpgradeScriptValidationTest(unittest.TestCase):
    def _run_upgrade_func(self, func_call, env=None, cwd=None):
        """Source upgrade.sh in test mode and run the given function call."""
        setup = f"""
KUBE_AGENTS_SOURCE_ONLY=true source "{_UPGRADE_SH}"
{func_call}
"""
        full_env = get_isolated_test_env(overrides=env or {})
        return subprocess.run(
            ["bash", "-c", setup],
            capture_output=True,
            text=True,
            env=full_env,
            cwd=str(cwd or _REPO_ROOT),
        )

    def test_validate_immutable_ref_accepts_valid_refs(self):
        for ref in VALID_IMMUTABLE_REFS:
            with self.subTest(ref=ref):
                cmd = f'validate_immutable_ref "{ref}"'
                proc = self._run_upgrade_func(cmd)
                self.assertEqual(
                    proc.returncode,
                    0,
                    f"upgrade.sh: expected ref '{ref}' to be valid, stderr: {proc.stderr}",
                )

    def test_validate_immutable_ref_rejects_invalid_refs(self):
        for ref in INVALID_IMMUTABLE_REFS:
            with self.subTest(ref=ref):
                cmd = f'validate_immutable_ref "{ref}"'
                proc = self._run_upgrade_func(cmd)
                self.assertNotEqual(
                    proc.returncode,
                    0,
                    f"upgrade.sh: expected ref '{ref}' to be rejected",
                )

    def test_piped_stdin_executes_main(self):
        """Ensures piped curl | bash invocations execute main and do not exit early."""
        upgrade_script_content = _UPGRADE_SH.read_text()
        proc = subprocess.run(
            ["bash", "-s", "--", "--help"],
            input=upgrade_script_content,
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
        )
        self.assertEqual(proc.returncode, 0, f"Piped execution failed: {proc.stderr}")
        self.assertIn(UPGRADER_HELP_BANNER, proc.stdout)

    def test_verify_local_source_ref_accepts_baked_release_in_non_git_dir(self):
        """Verifies verify_local_source_ref succeeds for unpacked release archive without Git repository."""
        import tempfile

        with tempfile.TemporaryDirectory(prefix="unpacked-upgrade-") as outer_dir:
            archive_dir = pathlib.Path(outer_dir) / "kube-agents-0.2.0"
            archive_dir.mkdir(parents=True)

            cmd = f'BAKED_RELEASE_VERSION="0.2.0"; verify_local_source_ref "{archive_dir}" "0.2.0"'
            proc = self._run_upgrade_func(cmd, cwd=archive_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Verified upgrade sources match baked official release 0.2.0", proc.stdout)

    def test_verify_local_source_ref_accepts_release_bundle_marker_in_non_git_dir(self):
        """Verifies verify_local_source_ref in upgrade.sh logs bundle provenance attribution when .release-bundle matches baked version."""
        import tempfile

        with tempfile.TemporaryDirectory(prefix="unpacked-upgrade-bundle-") as outer_dir:
            archive_dir = pathlib.Path(outer_dir) / f"kube-agents-{MOCK_RELEASE_BUNDLE_VERSION}"
            create_mock_release_bundle_marker(archive_dir)

            cmd = f'BAKED_RELEASE_VERSION="{MOCK_RELEASE_BUNDLE_VERSION}"; verify_local_source_ref "{archive_dir}" "{MOCK_RELEASE_BUNDLE_VERSION}"'
            proc = self._run_upgrade_func(cmd, cwd=archive_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(f"Verified upgrade sources match official release bundle {MOCK_RELEASE_BUNDLE_VERSION}", proc.stdout)

    def test_verify_local_source_ref_rejects_unbaked_release_bundle_marker_in_non_git_dir(self):
        """Verifies .release-bundle marker cannot bypass unversioned source directory rejection in upgrade.sh when baked version is empty."""
        import tempfile

        with tempfile.TemporaryDirectory(prefix="unpacked-unbaked-upgrade-") as outer_dir:
            archive_dir = pathlib.Path(outer_dir) / f"kube-agents-{MOCK_RELEASE_BUNDLE_VERSION}"
            create_mock_release_bundle_marker(archive_dir)

            cmd = f'BAKED_RELEASE_VERSION=""; verify_local_source_ref "{archive_dir}" "{MOCK_RELEASE_BUNDLE_VERSION}"'
            proc = self._run_upgrade_func(cmd, cwd=archive_dir)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Refusing to upgrade from an unversioned source directory", proc.stdout)

    def test_verify_local_source_ref_rejects_a_bundle_of_another_release(self):
        """A piped release script carries its own baked version wherever it runs.

        Standing in an unpacked 0.5.0 bundle and piping the 0.6.0 one-liner used
        to fall through matches_release_bundle_ref into "match baked official
        release 0.6.0", and then applied the old tree's Terraform and charts at
        the new tag. The directory says which release it is; that wins.
        """
        import tempfile

        with tempfile.TemporaryDirectory(prefix="unpacked-older-bundle-") as outer_dir:
            archive_dir = pathlib.Path(outer_dir) / "kube-agents-0.5.0"
            create_mock_release_bundle_marker(archive_dir, version="0.5.0")

            cmd = f'BAKED_RELEASE_VERSION="0.6.0"; verify_local_source_ref "{archive_dir}" "0.6.0"'
            proc = self._run_upgrade_func(cmd, cwd=archive_dir)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("it is release '0.5.0', not '0.6.0'", proc.stdout)
            self.assertNotIn("Verified", proc.stdout)

    def test_verify_local_source_ref_rejects_a_stale_tree_carrying_no_marker(self):
        """The marker is one way a tree names its release, not the only one.

        A copy of a bundle with .release-bundle removed, or a bundle from a
        release predating the marker, still carries the version the packager
        stamps into every root script. Keying the refusal on the marker alone
        let exactly those trees through with a green "verified".
        """
        import tempfile

        with tempfile.TemporaryDirectory(prefix="unmarked-older-tree-") as outer_dir:
            archive_dir = pathlib.Path(outer_dir) / "kube-agents-0.5.0"
            archive_dir.mkdir(parents=True)
            (archive_dir / "upgrade.sh").write_text('#!/usr/bin/env bash\nBAKED_RELEASE_VERSION="0.5.0"\n')

            cmd = f'BAKED_RELEASE_VERSION="0.6.0"; verify_local_source_ref "{archive_dir}" "0.6.0"'
            proc = self._run_upgrade_func(cmd, cwd=archive_dir)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("it is release '0.5.0', not '0.6.0'", proc.stdout)
            self.assertNotIn("Verified", proc.stdout)

    def test_verify_local_source_ref_accepts_a_marker_that_names_only_the_tag(self):
        """deploy/release-versioning.md promises a match on version *or* tag.

        Requiring version= before tag= was consulted turned a marker naming the
        requested release into a refusal of it.
        """
        import tempfile

        with tempfile.TemporaryDirectory(prefix="tag-only-bundle-") as outer_dir:
            archive_dir = pathlib.Path(outer_dir) / "kube-agents-0.6.0"
            archive_dir.mkdir(parents=True)
            (archive_dir / ".release-bundle").write_text("name=kube-agents\ntag=0.6.0\n")

            cmd = f'BAKED_RELEASE_VERSION="0.6.0"; verify_local_source_ref "{archive_dir}" "0.6.0"'
            proc = self._run_upgrade_func(cmd, cwd=archive_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("official release bundle 0.6.0", proc.stdout)

    def test_verify_local_source_ref_in_git_worktree_enforces_git_alignment(self):
        """Verifies verify_local_source_ref in upgrade.sh enforces clean git status in real git checkouts."""
        import tempfile

        with tempfile.TemporaryDirectory(prefix="git-upgrade-repo-") as repo_dir:
            repo_path = pathlib.Path(repo_dir)
            subprocess.run(["git", "init"], cwd=str(repo_path), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo_path), check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_path), check=True)
            (repo_path / "file.txt").write_text("initial\n")
            subprocess.run(["git", "add", "file.txt"], cwd=str(repo_path), check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo_path), check=True)
            subprocess.run(["git", "tag", "0.2.0"], cwd=str(repo_path), check=True)

            # Make checkout dirty
            (repo_path / "file.txt").write_text("dirty uncommitted change\n")

            cmd = f'BAKED_RELEASE_VERSION="0.2.0"; verify_local_source_ref "{repo_path}" "0.2.0"'
            proc = self._run_upgrade_func(cmd, cwd=repo_path)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("dirty checkout", proc.stdout)

    def test_a_plan_previews_a_dirty_checkout_instead_of_refusing_it(self):
        """--plan says it changes nothing, so a stray edit is something to report, not refuse.

        Previews reuse the install checkout once it is at the ref — the steady
        state after any successful upgrade — so refusing here would take the
        drift report away from the one command that answers "what have I
        edited". verify_local_source_clean already warns both previews.
        """
        import tempfile

        with tempfile.TemporaryDirectory(prefix="git-upgrade-plan-dirty-") as repo_dir:
            repo_path = pathlib.Path(repo_dir)
            subprocess.run(["git", "init"], cwd=str(repo_path), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo_path), check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_path), check=True)
            (repo_path / "file.txt").write_text("initial\n")
            subprocess.run(["git", "add", "file.txt"], cwd=str(repo_path), check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo_path), check=True)
            subprocess.run(["git", "tag", "0.2.0"], cwd=str(repo_path), check=True)
            (repo_path / "file.txt").write_text("a hand edit the operator wants to see planned\n")

            cmd = (
                'BAKED_RELEASE_VERSION="0.2.0"; PARAM_PLAN="true"; '
                f'verify_local_source_ref "{repo_path}" "0.2.0"'
            )
            proc = self._run_upgrade_func(cmd, cwd=repo_path)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("preview is using uncommitted source changes", proc.stdout)
            self.assertNotIn("Refusing", proc.stdout)


class UpgradeRunContractTest(unittest.TestCase):
    """Properties of the run main() performs, checked against the script source."""

    def test_health_verification_covers_the_pods_that_run_the_commands(self):
        """A healthy gateway is not a working install.

        The agent executes nothing in its own pod: shell commands go to the
        sandbox StatefulSet over ssh and credentialed ones through the proxy.
        Step 5 verified the gateway alone, so an upgrade that left either of
        those unready still printed "verified healthy" -- and the symptom
        arrives later, as an agent that cannot run kubectl.
        """
        source = _UPGRADE_SH.read_text()
        # Spelled through installer_common.sh's chart-contract constants, so the
        # constant's value is checked too: a renamed constant that no longer
        # holds the object's name would otherwise still pass.
        common = (_REPO_ROOT / "scripts" / "installer" / "installer_common.sh").read_text()
        for kind, constant, name in (
            ("statefulset", "PLATFORM_AGENT_SHELL_STATEFULSET", "platform-agent-shell"),
            ("deployment", "PLATFORM_AGENT_CREDENTIAL_PROXY_DEPLOYMENT", "platform-agent-credential-proxy"),
        ):
            with self.subTest(target=f"{kind}/{name}"):
                self.assertIn(f'readonly {constant}="{name}"', common)
                self.assertIn(f'kubectl rollout status "{kind}/${{{constant}}}"', source)

    def test_the_coordinate_overrides_reach_the_environment(self):
        """--project-id/--cluster-name/--region have to travel: the exports are
        what the credentials fetch, the tfvars generator and the helm release
        all read, and nothing else carries them."""
        source = _UPGRADE_SH.read_text()
        for var in ("PROJECT_ID", "CLUSTER_NAME", "REGION"):
            with self.subTest(var=var):
                self.assertIn(f'export {var}="$target_', source)


class DirtyCheckoutRefusalTest(unittest.TestCase):
    """A tagless upgrade still applies this checkout to a live install.

    `--image-tag` makes three refusals possible at once, and only the middle one
    — does HEAD match the requested ref — actually needs a tag. Gating the whole
    set on the tag's presence would let `--keep-image-tag` carry uncommitted
    edits to `terraform/` or `charts/` into a real `terraform apply`: an install
    running a composition that exists in no commit and that nobody can diff.
    """

    def _run(self, func_call, env=None, cwd=None):
        setup = (f'KUBE_AGENTS_SOURCE_ONLY=true source "{_UPGRADE_SH}"\n'
                 f"{func_call}\n")
        return subprocess.run(
            ["bash", "-c", setup], capture_output=True, text=True,
            env=get_isolated_test_env(overrides=env), cwd=str(cwd or _REPO_ROOT),
        )

    def _repo(self, tmp, dirty):
        """A real git checkout, clean or with a tracked file modified."""
        subprocess.run(["git", "init", "-q", tmp], check=True)
        for cmd in (["config", "user.email", "t@example.com"],
                    ["config", "user.name", "T"]):
            subprocess.run(["git", "-C", tmp, *cmd], check=True)
        target = os.path.join(tmp, "main.tf")
        with open(target, "w") as handle:
            handle.write("# committed\n")
        subprocess.run(["git", "-C", tmp, "add", "."], check=True)
        subprocess.run(["git", "-C", tmp, "commit", "-qm", "init"], check=True)
        if dirty:
            with open(target, "a") as handle:
                handle.write("# uncommitted local edit\n")
        return tmp

    def test_a_dirty_checkout_is_refused_without_a_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp, dirty=True)
            proc = self._run(f'verify_local_source_clean "{repo}"')
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("dirty checkout", proc.stdout + proc.stderr)

    def test_a_clean_checkout_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp, dirty=False)
            proc = self._run(f'verify_local_source_clean "{repo}"')
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_an_unversioned_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run(f'verify_local_source_clean "{tmp}"')
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("unversioned source directory", proc.stdout + proc.stderr)

    def test_the_previews_warn_instead_of_refusing(self):
        """--plan and --dry-run change nothing, and a plan of a tree mid-edit is
        the one command that answers "what have I changed here"."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp, dirty=True)
            for flag in ("PARAM_PLAN", "PARAM_DRY_RUN"):
                with self.subTest(flag=flag):
                    proc = self._run(
                        f'{flag}=true; verify_local_source_clean "{repo}"')
                    self.assertEqual(proc.returncode, 0,
                                     proc.stdout + proc.stderr)
                    self.assertIn("uncommitted source changes",
                                  proc.stdout + proc.stderr)

    def test_the_tagless_paths_call_it(self):
        """A run with no ref is verified clean, whichever arm found its sources.

        acquire_upgrade_sources verifies once, after the arms: with a ref it
        compares against the ref, and without one it still refuses a dirty tree,
        because a tagless run applies that tree to a live install all the same.
        """
        source = _UPGRADE_SH.read_text()
        self.assertEqual(source.count('verify_local_source_clean "$resolved_dir"'), 1)
        guard = source.index('if [ -n "$expected_ref" ]; then\n    verify_local_source_ref "$resolved_dir" "$expected_ref"\n  else\n    verify_local_source_clean "$resolved_dir"')
        self.assertLess(source.index("acquire_upgrade_sources() {"), guard)


class InteractiveImageTagPromptTest(unittest.TestCase):
    """A bare Enter at the tag prompt has to be a hard error.

    `--plan` and `--keep-image-tag` make the tag optional, so
    `validate_immutable_ref` — whose first branch rejects an empty ref — runs
    only when a tag is present. Nothing else catches an empty answer: without an
    explicit check it skips `verify_local_source_ref` (the dirty-checkout
    refusal) and silently becomes `--keep-image-tag`.

    Driven through a pty rather than asserted against the source, because the
    prompt reads from /dev/tty specifically so that it cannot be fed on stdin.
    """

    def _answer_prompt_with_enter(self):
        import pty
        import select

        pid, fd = pty.fork()
        if pid == 0:  # pragma: no cover - replaced by execve
            # os._exit, not an exception: a raise here would unwind inside a
            # forked copy of the test runner and report a second suite result.
            try:
                os.chdir(str(_REPO_ROOT))
                os.execve(
                    "/bin/bash",
                    ["bash", str(_UPGRADE_SH)],
                    {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                     "HOME": os.environ.get("HOME", "/tmp"), "TERM": "dumb"},
                )
            finally:
                os._exit(127)
        out = b""
        answered = False
        # A cap rather than a wait: if the guard ever regresses, the run does
        # not hang the suite, it proceeds and this fails on the exit code.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if ready:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:  # the child closed the pty
                    break
                if not chunk:
                    break
                out += chunk
            if not answered and b"Target image tag" in out:
                os.write(fd, b"\n")
                answered = True
        else:
            os.kill(pid, 9)
            self.fail("upgrade.sh did not exit within 30s of the empty answer")
        _, status = os.waitpid(pid, 0)
        self.assertTrue(answered, "the tag prompt never appeared")
        return status, out.decode(errors="replace")

    def test_a_bare_enter_at_the_prompt_aborts(self):
        status, out = self._answer_prompt_with_enter()
        self.assertTrue(os.WIFEXITED(status), f"upgrade.sh was signalled: {out}")
        self.assertEqual(os.WEXITSTATUS(status), 1, out)
        self.assertIn("--image-tag is required", out)
        # And it names the flag that asks for what an empty answer looked like
        # it might have meant, rather than leaving the reader to find it.
        self.assertIn("--keep-image-tag", out)

    def test_it_stops_before_touching_the_install(self):
        """Nothing may run between the empty answer and the exit."""
        _, out = self._answer_prompt_with_enter()
        for forbidden in ("get-credentials", "terraform", "helm"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, out)

    def test_full_upgrade_checks_service_account_ownership_before_the_apply(self):
        """A minter or Vertex GSA is first planned on the upgrade that enables it (#1294)."""
        text = (_REPO_ROOT / "upgrade.sh").read_text()
        check_idx = text.index("check_service_account_ownership || exit 1")
        apply_idx = text.index("apply -auto-approve -input=false")
        self.assertLess(check_idx, apply_idx)

    def test_upgrade_invokes_ensure_clean_helm_release(self):
        text = (_REPO_ROOT / "upgrade.sh").read_text()
        self.assertIn('ensure_clean_helm_release "$KUBE_AGENTS_HELM_RELEASE" "$target_namespace"', text)

    def test_upgrade_confirms_agent_image_before_rollout_status(self):
        text = (_REPO_ROOT / "upgrade.sh").read_text()
        confirm_idx = text.index('confirm_agent_image.sh" "$target_namespace" "$PLATFORM_AGENT_DEPLOYMENT"')
        rollout_idx = text.index('rollout status "deployment/${PLATFORM_AGENT_DEPLOYMENT}" -n "$target_namespace" --timeout=900s')
        self.assertLess(confirm_idx, rollout_idx)

    def test_upgrade_confirms_agent_image_scoped_to_harness_and_full_modes(self):
        text = (_REPO_ROOT / "upgrade.sh").read_text()
        self.assertIn('[ "$PARAM_UPGRADE_MODE" = "harness" ] || [ "$PARAM_UPGRADE_MODE" = "full" ]', text)
        self.assertIn('kubectl get deployment "$PLATFORM_AGENT_DEPLOYMENT" -n "$target_namespace"', text)



class UpgradeReusesTheInstallCheckoutTest(unittest.TestCase):
    """upgrade.sh moves the checkout install.sh left behind, rather than fetching its own.

    The install one-liner leaves its sources — and the install's install.env —
    in HOME/kube-agents. Before this, the upgrade one-liner fetched a fresh copy
    into a temporary directory, which has sources and no configuration, and the
    run then refused to upgrade without configuration. These tests mirror
    tests/test_install_script.py, whose refresh_existing_clone this one copies.
    """

    @staticmethod
    def _git(*args, cwd):
        return subprocess.run(
            ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
        ).stdout.strip()

    def _existing_clone_fixture(self, checked_out_tag, full_clone=False, with_install_env=True):
        """A clone of an earlier release under HOME, the way an install leaves one.

        A bare "upstream" holds tags 0.2.0 and 0.3.0, each tracking install.sh
        (the marker refresh_existing_clone requires) and the
        scripts/installer/installer_common.sh every kube-agents checkout has —
        the file the source-resolution arms test for, so a fixture without it
        would make those arms unreachable and the tests vacuous. The clone is
        taken while only 0.2.0 exists, so it has never seen 0.3.0 — the shape of
        a checkout from an earlier install. Returns (home_dir, clone_dir,
        upstream_url, {tag: commit}).
        """
        temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp_dir.cleanup)
        base = pathlib.Path(temp_dir.name)
        work_dir = base / "work"
        bare_dir = base / "upstream.git"
        home_dir = base / "home"
        clone_dir = home_dir / "kube-agents"
        home_dir.mkdir()
        git = self._git

        work_dir.mkdir()
        git("init", "-b", "main", cwd=work_dir)
        git("config", "user.name", "Test", cwd=work_dir)
        git("config", "user.email", "test@example.com", cwd=work_dir)
        git("config", "commit.gpgsign", "false", cwd=work_dir)
        (work_dir / "scripts" / "installer").mkdir(parents=True)
        (work_dir / "scripts" / "installer" / "installer_common.sh").write_text("# release 0.2.0\n")
        (work_dir / "install.sh").write_text("release 0.2.0\n")
        git("add", "install.sh", "scripts/installer/installer_common.sh", cwd=work_dir)
        git("commit", "-m", "release 0.2.0", cwd=work_dir)
        git("tag", "0.2.0", cwd=work_dir)
        git("clone", "--bare", "--quiet", str(work_dir), str(bare_dir), cwd=base)
        upstream_url = bare_dir.as_uri()
        if full_clone:
            git("clone", "--quiet", upstream_url, str(clone_dir), cwd=base)
        else:
            git("clone", "--quiet", "--filter=blob:none", "--no-checkout", upstream_url, str(clone_dir), cwd=base)

        (work_dir / "install.sh").write_text("release 0.3.0\n")
        (work_dir / "scripts" / "installer" / "installer_common.sh").write_text("# release 0.3.0\n")
        git("add", "install.sh", "scripts/installer/installer_common.sh", cwd=work_dir)
        git("commit", "-m", "release 0.3.0", cwd=work_dir)
        git("tag", "0.3.0", cwd=work_dir)
        git("push", "--quiet", upstream_url, "main", "--tags", cwd=work_dir)
        commits = {tag: git("rev-parse", f"{tag}^{{commit}}", cwd=work_dir) for tag in ("0.2.0", "0.3.0")}

        if full_clone:
            if checked_out_tag == "0.3.0":
                git("fetch", "--quiet", upstream_url, "+refs/tags/0.3.0:refs/tags/0.3.0", cwd=clone_dir)
            git("checkout", "--quiet", "--detach", checked_out_tag, cwd=clone_dir)
        else:
            refspec = f"+refs/tags/{checked_out_tag}:refs/tags/{checked_out_tag}"
            git("fetch", "--quiet", "--depth=1", upstream_url, refspec, cwd=clone_dir)
            git("checkout", "--quiet", "--detach", "FETCH_HEAD", cwd=clone_dir)
            self.assertEqual(git("rev-parse", "--is-shallow-repository", cwd=clone_dir), "true")
        if with_install_env:
            (clone_dir / "install.env").write_text('PROJECT_ID="my-gcp-project"\n')
        return home_dir, clone_dir, upstream_url, commits

    def _refresh_from_outside(self, home_dir, clone_dir, upstream_url, requested_ref):
        """Run refresh_existing_clone from a copy of upgrade.sh outside any checkout.

        KUBE_AGENTS_REPO_URL is overridden after sourcing, because upgrade.sh
        assigns it unconditionally.
        """
        outside_dir = home_dir.parent / "outside"
        outside_dir.mkdir(exist_ok=True)
        isolated_upgrade_sh = outside_dir / "upgrade.sh"
        isolated_upgrade_sh.write_text(_UPGRADE_SH.read_text())
        setup = f"""
KUBE_AGENTS_SOURCE_ONLY=true source "{isolated_upgrade_sh}"
KUBE_AGENTS_REPO_URL="{upstream_url}"
refresh_existing_clone "{clone_dir}" "{requested_ref}"
"""
        return subprocess.run(
            ["bash", "-c", setup],
            capture_output=True,
            text=True,
            env={"HOME": str(home_dir), "PATH": os.environ["PATH"]},
            cwd=str(outside_dir),
        )

    def _head_of(self, clone_dir):
        return self._git("rev-parse", "HEAD", cwd=clone_dir)

    def test_the_clone_is_moved_to_the_requested_release(self):
        """The upgrade one-liner's whole point: an install at N ends up at N+1."""
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")

        proc = self._refresh_from_outside(home_dir, clone_dir, upstream_url, "0.3.0")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("fetching '0.3.0'", proc.stdout)
        self.assertIn("Moved", proc.stdout)
        self.assertEqual(self._head_of(clone_dir), commits["0.3.0"])

    def test_the_install_env_in_the_clone_survives_the_move(self):
        """The configuration is why the clone is preferred, so the move must keep it."""
        home_dir, clone_dir, upstream_url, _ = self._existing_clone_fixture("0.2.0")
        (clone_dir / "install.env").write_text('PROJECT_ID="my-gcp-project"\n')

        proc = self._refresh_from_outside(home_dir, clone_dir, upstream_url, "0.3.0")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((clone_dir / "install.env").read_text(), 'PROJECT_ID="my-gcp-project"\n')

    def test_a_clone_already_at_the_release_is_not_fetched_into(self):
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")

        proc = self._refresh_from_outside(home_dir, clone_dir, upstream_url, "0.2.0")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("already at '0.2.0'", proc.stdout)
        self.assertEqual(self._head_of(clone_dir), commits["0.2.0"])

    def test_a_dirty_clone_is_left_alone(self):
        """Local changes are never fetched over; the run stops at verify_local_source_ref instead."""
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")
        (clone_dir / "install.sh").write_text("local edits\n")

        proc = self._refresh_from_outside(home_dir, clone_dir, upstream_url, "0.3.0")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("the checkout is dirty", proc.stdout)
        self.assertEqual(self._head_of(clone_dir), commits["0.2.0"])

    def test_a_directory_that_is_not_a_kube_agents_repository_is_left_alone(self):
        """A repository that merely shares the directory name is never moved."""
        home_dir, clone_dir, upstream_url, _ = self._existing_clone_fixture("0.2.0")
        unrelated = home_dir / "unrelated"
        unrelated.mkdir()
        self._git("init", "-b", "main", cwd=unrelated)
        self._git("config", "user.name", "Test", cwd=unrelated)
        self._git("config", "user.email", "test@example.com", cwd=unrelated)
        self._git("config", "commit.gpgsign", "false", cwd=unrelated)
        (unrelated / "README.md").write_text("not kube-agents\n")
        self._git("add", "README.md", cwd=unrelated)
        self._git("commit", "-m", "init", cwd=unrelated)
        before = self._head_of(unrelated)

        proc = self._refresh_from_outside(home_dir, unrelated, upstream_url, "0.3.0")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("is not a kube-agents revision", proc.stdout)
        self.assertEqual(self._head_of(unrelated), before)

    def test_a_complete_clone_does_not_become_shallow(self):
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0", full_clone=True)

        proc = self._refresh_from_outside(home_dir, clone_dir, upstream_url, "0.3.0")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._head_of(clone_dir), commits["0.3.0"])
        self.assertEqual(self._git("rev-parse", "--is-shallow-repository", cwd=clone_dir), "false")

    def test_the_tagless_arms_never_reach_the_clone(self):
        """--plan and --keep-image-tag still require a checkout; HOME is not a substitute.

        A CI job that checked out the ref it reconciles must keep that tree, so
        the preference for the installer's clone lives inside the arm that has a
        tag and no checkout, after the tagless refusal. The behaviour of that
        arm is covered below; this pins where it sits.
        """
        text = _UPGRADE_SH.read_text()
        tagless_refusal = text.index('--plan and --keep-image-tag have to run from a kube-agents checkout')
        clone_preference = text.index('refresh_existing_clone "$resolved_dir" "$expected_ref"')
        self.assertLess(tagless_refusal, clone_preference)

    def _acquire_from_outside(
        self, home_dir, upstream_url, requested_ref, preview_flag=None, cwd=None, baked_version=None
    ):
        """Run acquire_upgrade_sources from a copy of upgrade.sh outside any checkout.

        The copy is what makes the clone arm reachable: sourced from a directory
        with no scripts/installer/installer_common.sh, the script has no checkout
        of its own, which is the shape of `curl … | bash`.
        """
        outside_dir = home_dir.parent / "outside"
        outside_dir.mkdir(exist_ok=True)
        isolated_upgrade_sh = outside_dir / "upgrade.sh"
        isolated_upgrade_sh.write_text(_UPGRADE_SH.read_text())
        preview_line = f'{preview_flag}="true"' if preview_flag else ":"
        baked_line = f'BAKED_RELEASE_VERSION="{baked_version}"' if baked_version else ":"
        setup = f"""
KUBE_AGENTS_SOURCE_ONLY=true source "{isolated_upgrade_sh}"
KUBE_AGENTS_REPO_URL="{upstream_url}"
{preview_line}
{baked_line}
repo_dir=""
install_checkout=""
acquire_upgrade_sources repo_dir install_checkout "{requested_ref}"
echo "REPO_DIR=$repo_dir"
echo "INSTALL_CHECKOUT=$install_checkout"
"""
        return subprocess.run(
            ["bash", "-c", setup],
            capture_output=True,
            text=True,
            env={"HOME": str(home_dir), "PATH": os.environ["PATH"]},
            cwd=str(cwd or outside_dir),
        )

    @staticmethod
    def _reported(proc, key):
        for line in proc.stdout.splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1]
        return None

    def test_the_install_checkout_becomes_the_upgrade_sources(self):
        """The documented one-liner: no checkout of its own, so it uses the install's."""
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")

        proc = self._acquire_from_outside(home_dir, upstream_url, "0.3.0")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._reported(proc, "REPO_DIR"), str(clone_dir))
        self.assertEqual(self._reported(proc, "INSTALL_CHECKOUT"), str(clone_dir))
        self.assertEqual(self._head_of(clone_dir), commits["0.3.0"])

    def test_a_plain_directory_at_the_clone_path_is_not_adopted(self):
        """A stale bundle or a copied tree in HOME is not verified release sources.

        verify_local_source_ref accepts anything that is not a Git worktree once
        the baked version equals the requested ref — the default on a release
        copy — so adopting the directory on its existence alone would announce
        an unrelated tree as verified and then apply it to a live install.
        """
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")
        stale_bundle = home_dir / "kube-agents-plain"
        stale_bundle.mkdir()
        (stale_bundle / "install.sh").write_text("an unpacked bundle of some other release\n")
        # Put the plain directory where the upgrader looks.
        clone_dir.rename(home_dir / "kube-agents-real")
        stale_bundle.rename(clone_dir)

        proc = self._acquire_from_outside(home_dir, upstream_url, "0.3.0", baked_version="0.3.0")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._reported(proc, "INSTALL_CHECKOUT"), "")
        self.assertNotEqual(self._reported(proc, "REPO_DIR"), str(clone_dir))
        self.assertNotIn("baked official release", proc.stdout)
        self.assertEqual((clone_dir / "install.sh").read_text(), "an unpacked bundle of some other release\n")
        self.assertIn(commits["0.3.0"], proc.stdout)

    def test_an_unrelated_repository_at_the_clone_path_is_not_adopted(self):
        """Sharing the directory name or a generic root install.sh is not enough; HEAD has to track kube-agents' own installer layout."""
        home_dir, clone_dir, upstream_url, _ = self._existing_clone_fixture("0.2.0")
        clone_dir.rename(home_dir / "kube-agents-real")
        clone_dir.mkdir()
        self._git("init", "-b", "main", cwd=clone_dir)
        self._git("config", "user.name", "Test", cwd=clone_dir)
        self._git("config", "user.email", "test@example.com", cwd=clone_dir)
        self._git("config", "commit.gpgsign", "false", cwd=clone_dir)
        (clone_dir / "README.md").write_text("not kube-agents\n")
        (clone_dir / "install.sh").write_text("#!/usr/bin/env bash\necho foreign installer\n")
        self._git("add", "README.md", "install.sh", cwd=clone_dir)
        self._git("commit", "-m", "init", cwd=clone_dir)
        before = self._head_of(clone_dir)

        proc = self._acquire_from_outside(home_dir, upstream_url, "0.3.0")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._reported(proc, "INSTALL_CHECKOUT"), "")
        self.assertNotEqual(self._reported(proc, "REPO_DIR"), str(clone_dir))
        self.assertEqual(self._head_of(clone_dir), before)

    def test_a_plan_does_not_move_the_install_checkout(self):
        """--plan says it changes nothing, and the operator's checkout is part of nothing."""
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")

        proc = self._acquire_from_outside(home_dir, upstream_url, "0.3.0", preview_flag="PARAM_PLAN")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._head_of(clone_dir), commits["0.2.0"])
        self.assertNotEqual(self._reported(proc, "REPO_DIR"), str(clone_dir))
        # Still found, because the install's configuration lives in it.
        self.assertEqual(self._reported(proc, "INSTALL_CHECKOUT"), str(clone_dir))
        self.assertIn("a preview does not move it", proc.stdout)

    def test_a_dry_run_does_not_move_the_install_checkout(self):
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")

        proc = self._acquire_from_outside(home_dir, upstream_url, "0.3.0", preview_flag="PARAM_DRY_RUN")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._head_of(clone_dir), commits["0.2.0"])
        self.assertNotEqual(self._reported(proc, "REPO_DIR"), str(clone_dir))

    def test_a_preview_uses_the_checkout_when_nothing_has_to_move(self):
        """Fetching a second copy of what is already there would be waste, not safety."""
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")

        proc = self._acquire_from_outside(home_dir, upstream_url, "0.2.0", preview_flag="PARAM_PLAN")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._reported(proc, "REPO_DIR"), str(clone_dir))
        self.assertEqual(self._head_of(clone_dir), commits["0.2.0"])
        self.assertIn("already at '0.2.0'", proc.stdout)

    def test_a_run_from_outside_standing_in_the_install_checkout_moves_it(self):
        """Standing in ~/kube-agents when running an outside copy of upgrade.sh moves the checkout rather than failing the ref check."""
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")

        proc = self._acquire_from_outside(home_dir, upstream_url, "0.3.0", cwd=clone_dir)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._reported(proc, "REPO_DIR"), str(clone_dir))
        self.assertEqual(self._reported(proc, "INSTALL_CHECKOUT"), str(clone_dir))
        self.assertEqual(self._head_of(clone_dir), commits["0.3.0"])

    def test_a_plan_from_outside_standing_in_the_install_checkout_does_not_move_it(self):
        """An outside --plan copy run while standing in ~/kube-agents keeps it at its current ref and still finds install.env."""
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")

        proc = self._acquire_from_outside(
            home_dir, upstream_url, "0.3.0", preview_flag="PARAM_PLAN", cwd=clone_dir
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._head_of(clone_dir), commits["0.2.0"])
        self.assertNotEqual(self._reported(proc, "REPO_DIR"), str(clone_dir))
        self.assertEqual(self._reported(proc, "INSTALL_CHECKOUT"), str(clone_dir))
        self.assertIn("a preview does not move it", proc.stdout)

    def test_a_missing_cli_tool_does_not_move_the_install_checkout(self):
        """Like install.sh, missing CLI tools and conflicting preview flags fail before acquire_upgrade_sources moves ~/kube-agents."""
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")
        outside_dir = home_dir.parent / "outside"
        outside_dir.mkdir(exist_ok=True)
        sterile_bin = home_dir.parent / "sterile-bin"
        sterile_bin.mkdir(exist_ok=True)
        isolated_upgrade_sh = outside_dir / "upgrade.sh"
        isolated_upgrade_sh.write_text(
            _UPGRADE_SH.read_text().replace(
                'KUBE_AGENTS_REPO_URL="https://github.com/gke-labs/kube-agents.git"',
                f'KUBE_AGENTS_REPO_URL="{upstream_url}"',
            )
        )
        proc = subprocess.run(
            [shutil.which("bash") or "/bin/bash", str(isolated_upgrade_sh), "--image-tag=0.3.0", "--non-interactive"],
            capture_output=True,
            text=True,
            env=get_isolated_test_env(overrides={"HOME": str(home_dir), "PATH": str(sterile_bin)}),
            cwd=str(outside_dir),
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Required CLI tool", proc.stdout + proc.stderr)
        self.assertEqual(self._head_of(clone_dir), commits["0.2.0"])

        conflicting = subprocess.run(
            [
                shutil.which("bash") or "/bin/bash",
                str(isolated_upgrade_sh),
                "--image-tag=0.3.0",
                "--dry-run",
                "--plan",
                "--non-interactive",
            ],
            capture_output=True,
            text=True,
            env=get_isolated_test_env(overrides={"HOME": str(home_dir), "PATH": str(sterile_bin)}),
            cwd=str(outside_dir),
        )
        self.assertEqual(conflicting.returncode, 1)
        self.assertIn("--dry-run and --plan are different previews", conflicting.stdout + conflicting.stderr)
        self.assertEqual(self._head_of(clone_dir), commits["0.2.0"])

    def _acquire_through_a_real_pipe(
        self, home_dir, upstream_url, requested_ref, preview_flag=None, cwd=None, extra_env=None
    ):
        """Run acquire_upgrade_sources with the script arriving on stdin.

        The distinction this makes against _acquire_from_outside is the point:
        there the script is a file, so BASH_SOURCE[0] names it. Under
        `curl … | bash` there is no file, and bash fills BASH_SOURCE[0] inside a
        function with the name the shell was invoked as ("bash") — which
        dirname turns into the invocation directory. Sourcing a copy therefore
        cannot reach the arms a real pipe takes.
        """
        preview_line = f'{preview_flag}="true"' if preview_flag else ":"
        piped = "\n".join(
            [
                "KUBE_AGENTS_SOURCE_ONLY=true",
                _UPGRADE_SH.read_text(),
                f'KUBE_AGENTS_REPO_URL="{upstream_url}"',
                preview_line,
                'repo_dir=""',
                'install_checkout=""',
                f'acquire_upgrade_sources repo_dir install_checkout "{requested_ref}"',
                'echo "REPO_DIR=$repo_dir"',
                'echo "INSTALL_CHECKOUT=$install_checkout"',
            ]
        )
        run_env = {"HOME": str(home_dir), "PATH": os.environ["PATH"]}
        if extra_env:
            run_env.update(extra_env)
        return subprocess.run(
            ["bash", "-s"],
            input=piped,
            capture_output=True,
            text=True,
            env=run_env,
            cwd=str(cwd or home_dir),
        )

    def test_a_real_pipe_from_inside_the_install_checkout_moves_it(self):
        """The documented one-liner, run the way the docs say: standing in the install checkout.

        BASH_SOURCE[0] is non-empty here, so the guard cannot be "is it set";
        it has to be "does it name a file".
        """
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")

        proc = self._acquire_through_a_real_pipe(home_dir, upstream_url, "0.3.0", cwd=clone_dir)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._reported(proc, "REPO_DIR"), str(clone_dir))
        self.assertEqual(self._reported(proc, "INSTALL_CHECKOUT"), str(clone_dir))
        self.assertEqual(self._head_of(clone_dir), commits["0.3.0"])

    def test_a_real_pipe_plan_from_inside_the_install_checkout_does_not_move_it(self):
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")

        proc = self._acquire_through_a_real_pipe(
            home_dir, upstream_url, "0.3.0", preview_flag="PARAM_PLAN", cwd=clone_dir
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._head_of(clone_dir), commits["0.2.0"])
        self.assertNotEqual(self._reported(proc, "REPO_DIR"), str(clone_dir))
        self.assertEqual(self._reported(proc, "INSTALL_CHECKOUT"), str(clone_dir))

    def test_a_real_pipe_from_a_neutral_directory_still_finds_the_install_checkout(self):
        """Nothing in the invocation directory, so HOME's checkout is the one to move."""
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")
        neutral = home_dir.parent / "neutral"
        neutral.mkdir(exist_ok=True)

        proc = self._acquire_through_a_real_pipe(home_dir, upstream_url, "0.3.0", cwd=neutral)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._reported(proc, "REPO_DIR"), str(clone_dir))
        self.assertEqual(self._head_of(clone_dir), commits["0.3.0"])

    def test_a_real_pipe_in_a_dev_clone_without_install_env_moves_the_home_checkout(self):
        """A clean dev clone in $(pwd) carrying no install.env yields to ~/kube-agents."""
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")
        (clone_dir / "install.env").write_text('CLUSTER_NAME="home-install"\n')
        dev_clone = home_dir.parent / "dev-clone"
        self._git("clone", "--branch", "0.2.0", str(upstream_url), str(dev_clone), cwd=home_dir)

        proc = self._acquire_through_a_real_pipe(home_dir, upstream_url, "0.3.0", cwd=dev_clone)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._head_of(dev_clone), commits["0.2.0"])
        self.assertEqual(self._reported(proc, "REPO_DIR"), str(clone_dir))
        self.assertEqual(self._reported(proc, "INSTALL_CHECKOUT"), str(clone_dir))
        self.assertEqual(self._head_of(clone_dir), commits["0.3.0"])

    def test_a_real_pipe_in_a_dev_clone_with_no_home_checkout_does_not_move_the_dev_clone(self):
        """When neither $(pwd) nor ~/kube-agents holds install.env, a piped run does not detach the developer clone."""
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")
        shutil.rmtree(clone_dir)
        dev_clone = home_dir.parent / "dev-clone"
        self._git("clone", "--branch", "0.2.0", str(upstream_url), str(dev_clone), cwd=home_dir)

        proc = self._acquire_through_a_real_pipe(home_dir, upstream_url, "0.3.0", cwd=dev_clone)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._head_of(dev_clone), commits["0.2.0"])
        self.assertEqual(self._reported(proc, "INSTALL_CHECKOUT"), "")
        self.assertNotEqual(self._reported(proc, "REPO_DIR"), str(dev_clone))

    def test_a_run_configured_from_pwd_install_env_does_not_adopt_the_home_checkout(self):
        """Standing in install B's directory (with install.env) fetches a temporary copy rather than moving or writing into ~/kube-agents."""
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")
        (clone_dir / "install.env").write_text('CLUSTER_NAME="install-a"\n')
        install_b = home_dir.parent / "install-b"
        install_b.mkdir(exist_ok=True)
        (install_b / "install.env").write_text('CLUSTER_NAME="install-b"\n')

        proc = self._acquire_through_a_real_pipe(home_dir, upstream_url, "0.3.0", cwd=install_b)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._head_of(clone_dir), commits["0.2.0"])
        self.assertEqual(self._reported(proc, "INSTALL_CHECKOUT"), "")
        self.assertNotEqual(self._reported(proc, "REPO_DIR"), str(clone_dir))

    def test_a_run_configured_from_explicit_install_env_does_not_adopt_the_home_checkout(self):
        """An explicit KUBE_AGENTS_INSTALL_ENV outside ~/kube-agents does not adopt ~/kube-agents; pointing at ~/kube-agents/install.env does."""
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0")
        (clone_dir / "install.env").write_text('CLUSTER_NAME="install-a"\n')
        external_env = home_dir.parent / "ci-install.env"
        external_env.write_text('CLUSTER_NAME="ci-install"\n')
        neutral = home_dir.parent / "neutral"
        neutral.mkdir(exist_ok=True)

        external_proc = self._acquire_through_a_real_pipe(
            home_dir,
            upstream_url,
            "0.3.0",
            cwd=neutral,
            extra_env={"KUBE_AGENTS_INSTALL_ENV": str(external_env)},
        )
        self.assertEqual(external_proc.returncode, 0, external_proc.stderr)
        self.assertEqual(self._head_of(clone_dir), commits["0.2.0"])
        self.assertEqual(self._reported(external_proc, "INSTALL_CHECKOUT"), "")
        self.assertNotEqual(self._reported(external_proc, "REPO_DIR"), str(clone_dir))

        home_proc = self._acquire_through_a_real_pipe(
            home_dir,
            upstream_url,
            "0.3.0",
            cwd=neutral,
            extra_env={"KUBE_AGENTS_INSTALL_ENV": str(clone_dir / "install.env")},
        )
        self.assertEqual(home_proc.returncode, 0, home_proc.stderr)
        self.assertEqual(self._head_of(clone_dir), commits["0.3.0"])
        self.assertEqual(self._reported(home_proc, "INSTALL_CHECKOUT"), str(clone_dir))
        self.assertEqual(self._reported(home_proc, "REPO_DIR"), str(clone_dir))

    def test_a_home_checkout_without_install_env_is_not_moved(self):
        """A clone in ~/kube-agents carrying no install.env is not detached onto the release before main() refuses."""
        home_dir, clone_dir, upstream_url, commits = self._existing_clone_fixture("0.2.0", with_install_env=False)
        neutral = home_dir.parent / "neutral"
        neutral.mkdir(exist_ok=True)

        proc = self._acquire_through_a_real_pipe(home_dir, upstream_url, "0.3.0", cwd=neutral)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._head_of(clone_dir), commits["0.2.0"])
        self.assertEqual(self._reported(proc, "INSTALL_CHECKOUT"), "")
        self.assertNotEqual(self._reported(proc, "REPO_DIR"), str(clone_dir))


class ConfigurationLookupOrderTest(unittest.TestCase):
    """Which install.env an upgrade loads, when more than one is reachable.

    install.sh resolves: KUBE_AGENTS_INSTALL_ENV -> the script's own checkout ->
    $(pwd) -> the clone in HOME. The upgrade had only the first and the last, and
    when it gained $(pwd) it put the HOME checkout ahead of it — so with two
    installs on one workstation, standing in B's directory and passing
    --cluster-name=B loaded A's chat space, allowed users, model provider,
    NAMESPACE and GitOps repo out of ~/kube-agents/install.env.
    """

    _INSTALLER_COMMON = _REPO_ROOT / "scripts" / "installer" / "installer_common.sh"

    def _resolve(self, repo_dir, install_checkout, cwd, install_env_var=None):
        setup = f"""
KUBE_AGENTS_SOURCE_ONLY=true source "{_UPGRADE_SH}"
# default_install_env_file is the last candidate, and it lives here.
source "{self._INSTALLER_COMMON}"
resolve_install_env_file "{repo_dir}" "{install_checkout}"
"""
        env = {"HOME": str(cwd), "PATH": os.environ["PATH"]}
        if install_env_var is not None:
            env["KUBE_AGENTS_INSTALL_ENV"] = str(install_env_var)
        proc = subprocess.run(
            ["bash", "-c", setup],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(cwd),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def _layout(self):
        """A run's own sources, the operator's working directory, and the install checkout."""
        temp_dir = tempfile.TemporaryDirectory(prefix="install-env-order-")
        self.addCleanup(temp_dir.cleanup)
        base = pathlib.Path(temp_dir.name)
        for name in ("sources", "cwd", "checkout"):
            (base / name).mkdir()
        return base

    def test_the_explicit_pointer_wins(self):
        base = self._layout()
        named = base / "named.env"
        named.write_text("")
        (base / "sources" / "install.env").write_text("")
        (base / "cwd" / "install.env").write_text("")
        (base / "checkout" / "install.env").write_text("")

        resolved = self._resolve(base / "sources", base / "checkout", base / "cwd", install_env_var=named)

        self.assertEqual(resolved, str(named))

    def test_the_run_s_own_checkout_wins_over_the_working_directory(self):
        """Running ./upgrade.sh from a checkout loads that checkout's configuration."""
        base = self._layout()
        (base / "sources" / "install.env").write_text("")
        (base / "cwd" / "install.env").write_text("")

        resolved = self._resolve(base / "sources", base / "checkout", base / "cwd")

        self.assertEqual(resolved, str(base / "sources" / "install.env"))

    def test_the_working_directory_wins_over_the_install_checkout(self):
        """The regression this order exists to stop: two installs, one $HOME/kube-agents.

        The piped run's sources ARE the install checkout, so preferring the
        sources' directory would silently load the wrong install's chat space,
        allowed users and namespace.
        """
        base = self._layout()
        (base / "cwd" / "install.env").write_text("")
        (base / "checkout" / "install.env").write_text("")

        resolved = self._resolve(base / "checkout", base / "checkout", base / "cwd")

        self.assertEqual(resolved, str(base / "cwd" / "install.env"))

    def test_the_install_checkout_is_used_when_nothing_is_nearer(self):
        """The documented one-liner, run from a directory with no configuration in it."""
        base = self._layout()
        (base / "checkout" / "install.env").write_text("")

        resolved = self._resolve(base / "checkout", base / "checkout", base / "cwd")

        self.assertEqual(resolved, str(base / "checkout" / "install.env"))

    def test_with_nothing_anywhere_it_names_the_sources_directory(self):
        """Nothing to load: the refusal that follows names where one would live."""
        base = self._layout()

        resolved = self._resolve(base / "sources", "", base / "cwd")

        self.assertEqual(resolved, str(base / "sources" / "install.env"))

    def test_upgrade_never_references_retired_vars_sh(self):
        """k8s-operator/scripts/vars.sh is retired and never read, written, or inspected by upgrade.sh or installer_common.sh."""
        for path in (_UPGRADE_SH, self._INSTALLER_COMMON):
            with self.subTest(file=path.name):
                text = path.read_text()
                self.assertNotIn("k8s-operator/scripts/vars.sh", text)
                self.assertNotIn("load_legacy_vars_file", text)


class FrontDoorsAgreeOnTheInstallCheckoutTest(unittest.TestCase):
    """install.sh and upgrade.sh have to find and move the same checkout.

    These helpers cannot live in scripts/installer/installer_common.sh, which is
    sourced out of the very checkout they go and find, so each front door
    carries a copy — the arrangement installer_common.sh already describes for
    the install.env loader. Copies drift, so they are pinned here.
    """

    _INSTALL_SH = _REPO_ROOT / "install.sh"

    @staticmethod
    def _function_text(source, name):
        opening = f"\n{name}() {{\n"
        start = source.index(opening) + 1
        end = source.index("\n}\n", start) + len("\n}\n")
        return source[start:end]

    def test_the_helpers_are_identical(self):
        install_sh = self._INSTALL_SH.read_text()
        upgrade_sh = _UPGRADE_SH.read_text()
        for name in ("fetch_source_ref", "refresh_existing_clone"):
            with self.subTest(function=name):
                self.assertEqual(
                    self._function_text(install_sh, name),
                    self._function_text(upgrade_sh, name),
                    f"{name} has drifted between install.sh and upgrade.sh",
                )

    def test_the_clone_constants_are_identical(self):
        install_sh = self._INSTALL_SH.read_text()
        upgrade_sh = _UPGRADE_SH.read_text()
        for constant in ("KUBE_AGENTS_CLONE_MARKER", "KUBE_AGENTS_FETCH_DEPTH_OPT"):
            with self.subTest(constant=constant):
                pattern = rf'^{constant}="([^"]+)"$'
                install_value = re.search(pattern, install_sh, re.MULTILINE)
                upgrade_value = re.search(pattern, upgrade_sh, re.MULTILINE)
                self.assertIsNotNone(install_value, f"install.sh does not declare {constant}")
                self.assertIsNotNone(upgrade_value, f"upgrade.sh does not declare {constant}")
                self.assertEqual(install_value.group(1), upgrade_value.group(1))

    def test_both_front_doors_name_the_same_directory(self):
        """The message HOME:? carries differs; the path it builds may not."""
        with tempfile.TemporaryDirectory(prefix="front-doors-install-env-") as env_dir:
            empty_install_env = pathlib.Path(env_dir) / "install.env"
            empty_install_env.write_text("")
            paths = {}
            for script in (self._INSTALL_SH, _UPGRADE_SH):
                proc = subprocess.run(
                    ["bash", "-c", f'KUBE_AGENTS_SOURCE_ONLY=true source "{script}"; kube_agents_clone_dir'],
                    capture_output=True,
                    text=True,
                    env={
                        "HOME": "/h",
                        "PATH": os.environ["PATH"],
                        "KUBE_AGENTS_INSTALL_ENV": str(empty_install_env),
                    },
                    cwd=str(_REPO_ROOT),
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                paths[script.name] = proc.stdout.strip()
        self.assertEqual(paths["install.sh"], "/h/kube-agents")
        self.assertEqual(paths["upgrade.sh"], paths["install.sh"])


class PipedUpgradeResolvesItsSourcesTest(unittest.TestCase):
    def test_a_piped_run_does_not_abort_on_an_unset_bash_source(self):
        """Under `curl | bash` with `set -u`, BASH_SOURCE[0] may name no file.

        install.sh has always defaulted it; upgrade.sh did not, so the source
        resolution could abort with an unbound variable instead of reporting
        what was wrong. --keep-image-tag reaches that resolution and then stops
        for its own, expected reason.
        """
        with tempfile.TemporaryDirectory(prefix="outside-checkout-") as outside:
            empty_install_env = pathlib.Path(outside) / "pinned-install.env"
            empty_install_env.write_text("")
            # main() checks its CLI tools before resolving sources, the way
            # install.sh does. Stub them, so this test reads the source
            # resolution it is about on any host, rather than whichever tool the
            # runner happens to be missing.
            stub_bin = pathlib.Path(outside) / "bin"
            stub_bin.mkdir()
            for tool in ("gcloud", "kubectl", "helm", "terraform"):
                stub = stub_bin / tool
                stub.write_text("#!/usr/bin/env bash\nexit 0\n")
                stub.chmod(0o755)
            proc = subprocess.run(
                ["bash", "-s", "--", "--keep-image-tag", "--non-interactive", "--project-id=my-gcp-project"],
                input=_UPGRADE_SH.read_text(),
                capture_output=True,
                text=True,
                env=get_isolated_test_env(
                    overrides={"KUBE_AGENTS_INSTALL_ENV": str(empty_install_env)},
                    bin_dir=str(stub_bin),
                ),
                cwd=outside,
            )
        combined = proc.stdout + proc.stderr
        self.assertNotIn("unbound variable", combined)
        self.assertNotIn("Required CLI tool", combined)
        self.assertIn("have to run from a kube-agents checkout", combined)


if __name__ == "__main__":
    unittest.main()
