"""The CI-side install configuration renderer.

`install.env` is the installer's input, and a GitHub runner has none — so every
job that drives the installer renders one from the environment's variables. What
that renderer gets wrong is not visible in its own output: an omitted setting
becomes a default, the default becomes a `terraform.tfvars` value, and
`terraform apply` plans the destruction of whatever the default did not mention.
That is #1060, and against a scheduled unattended apply on a long-lived
environment it is #1117's hardest blocker.

So these pin the two halves that keep it from happening: what --strict refuses,
and that an unset setting is left OUT of the file rather than written empty.
"""

import os
import pathlib
import subprocess
import tempfile
import unittest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "release" / "render_install_env.sh"

# The coordinates every invocation needs. Individual tests add to these.
_COORDS = {
    "GCP_PROJECT_ID": "kube-agents-autopush",
    "GCP_REGION": "us-central1",
    "GKE_CLUSTER_NAME": "platform-agent-host",
}

# Everything --strict additionally requires, at plausible values.
_STRICT_SETTINGS = {
    "GOOGLE_CHAT_ENABLED": "true",
    "MODEL_PROVIDER": "gemini",
    "PLATFORM_AGENT_PERMISSION_SET": "custom",
    "ENABLE_GVISOR": "true",
    "MEMORY_PROVIDER": "kube_agents_memory",
    "USER_PROFILE_ENABLED": "true",
    "ENABLE_GKE_BACKUP_PLAN": "true",
}


def render(env, strict=False):
    """Runs the renderer and returns (returncode, stdout+stderr, rendered text)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "install.env")
        args = [str(_SCRIPT), out]
        if strict:
            args.append("--strict")
        # A bare environment, so a variable the test did not set cannot arrive
        # from the developer's shell or from CI's own.
        proc = subprocess.run(
            args, capture_output=True, text=True,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env},
        )
        text = pathlib.Path(out).read_text() if os.path.exists(out) else ""
    return proc.returncode, proc.stdout + proc.stderr, text


def parse(text):
    """The rendered file as a dict, the way `set -a; . install.env` would read it."""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, raw = line.partition("=")
        # The renderer writes with %q, so bash is the only correct reader.
        values[key] = subprocess.run(
            ["bash", "-c", 'printf "%s" ' + raw], capture_output=True, text=True
        ).stdout
    return values


class RequiredInputsTest(unittest.TestCase):
    def test_missing_coordinates_fail_without_strict(self):
        rc, log, _ = render({"GCP_PROJECT_ID": "p"})
        self.assertEqual(rc, 1)
        self.assertIn("GCP_REGION", log)
        self.assertIn("GKE_CLUSTER_NAME", log)

    def test_every_missing_variable_is_named_in_one_message(self):
        """One run per missing variable is eleven runs on an unconfigured environment."""
        rc, log, _ = render(dict(_COORDS), strict=True)
        self.assertEqual(rc, 1)
        for var in _STRICT_SETTINGS:
            with self.subTest(var=var):
                self.assertIn(var, log)

    def test_the_failure_is_a_github_error_annotation(self):
        """So the reason shows on the run summary rather than only in the log."""
        rc, log, _ = render(dict(_COORDS), strict=True)
        self.assertEqual(rc, 1)
        self.assertIn("::error title=Install configuration is incomplete::", log)

    def test_coordinates_alone_are_enough_without_strict(self):
        """The ephemeral path: nothing exists yet, so nothing can be destroyed."""
        rc, log, text = render(dict(_COORDS))
        self.assertEqual(rc, 0, log)
        self.assertEqual(parse(text)["PROJECT_ID"], "kube-agents-autopush")

    def test_strict_passes_once_every_setting_is_present(self):
        rc, log, text = render({**_COORDS, **_STRICT_SETTINGS}, strict=True)
        self.assertEqual(rc, 0, log)
        self.assertEqual(parse(text)["PLATFORM_AGENT_PERMISSION_SET"], "custom")


class RenderingTest(unittest.TestCase):
    def test_an_unset_setting_is_omitted_rather_than_written_empty(self):
        """`KEY=` in install.env beats install.defaults.env and means "nothing".

        Which for MEMORY or PLATFORM_AGENT_PERMISSION_SET is a different install
        from the default one — and the difference is what an apply would destroy.
        """
        rc, log, text = render(dict(_COORDS))
        self.assertEqual(rc, 0, log)
        rendered = parse(text)
        self.assertNotIn("MODEL_DEFAULT_NAME", rendered)
        self.assertNotIn("SLACK_BOT_TOKEN", rendered)
        self.assertNotIn("MEMORY", rendered)

    def test_the_github_side_names_are_translated_to_installer_names(self):
        rc, log, text = render(dict(_COORDS))
        self.assertEqual(rc, 0, log)
        rendered = parse(text)
        self.assertEqual(rendered["PROJECT_ID"], "kube-agents-autopush")
        self.assertEqual(rendered["REGION"], "us-central1")
        self.assertEqual(rendered["CLUSTER_NAME"], "platform-agent-host")
        self.assertNotIn("GCP_PROJECT_ID", rendered)

    def test_memory_provider_maps_to_the_installers_vocabulary(self):
        for provider, expected in (
            ("kube_agents_memory", "hindsight"),
            ("hindsight", "hindsight"),
            ("none", "off"),
            ("off", "off"),
            ("multiuser_memory", "file"),
            ("anything-else", "file"),
        ):
            with self.subTest(provider=provider):
                _, _, text = render({**_COORDS, "MEMORY_PROVIDER": provider})
                self.assertEqual(parse(text)["MEMORY"], expected)

    def test_staging_spells_the_namespace_without_the_agent_prefix(self):
        """rc and nightly set AGENT_NAMESPACE; staging sets NAMESPACE.

        Both have installs running against them, so neither can be renamed in
        the GitHub UI without a window where the reconcile reads an empty value.
        """
        _, _, text = render({**_COORDS, "NAMESPACE": "kubeagents-system"})
        self.assertEqual(parse(text)["NAMESPACE"], "kubeagents-system")

        _, _, text = render({**_COORDS, "AGENT_NAMESPACE": "other-ns"})
        self.assertEqual(parse(text)["NAMESPACE"], "other-ns")

    def test_a_value_with_shell_syntax_in_it_survives_a_round_trip(self):
        """install.env is SOURCED, so an unquoted value is executed, not read."""
        hostile = "a b; echo pwned $HOME `id`"
        _, _, text = render({**_COORDS, "SLACK_HOME_CHANNEL_NAME": hostile})
        self.assertEqual(parse(text)["SLACK_HOME_CHANNEL_NAME"], hostile)

    def test_the_file_is_not_readable_by_anyone_else(self):
        """It carries the model provider's API key and the Slack tokens."""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "install.env")
            subprocess.run(
                [str(_SCRIPT), out], capture_output=True, text=True,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **_COORDS},
                check=True,
            )
            self.assertEqual(os.stat(out).st_mode & 0o077, 0)

    def test_secret_values_are_never_echoed(self):
        """The listing it prints is keys only; the job log is world-readable."""
        rc, log, _ = render({**_COORDS, "GEMINI_API_KEY": "sk-not-a-real-key"})
        self.assertEqual(rc, 0, log)
        self.assertNotIn("sk-not-a-real-key", log)
        self.assertIn("GEMINI_API_KEY", log)


if __name__ == "__main__":
    unittest.main()
