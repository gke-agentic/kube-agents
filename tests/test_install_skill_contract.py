"""install-kube-agents/SKILL.md puts the dry run in front of the apply.

An agent follows a skill top to bottom, and the first `install.sh` command it
reads is the one it runs. When that command is an apply, the agent provisions a
project before the operator has seen what the run would do, and the rule that
an existing cluster is never modified without the operator's say-so arrives
after the fact. These tests pin the order: a `--dry-run` (or `--generate-only`)
invocation appears before any invocation that applies, and the apply that
follows the dry run carries the same flags, so what runs is what was previewed.
Outside the install workflow, every `install.sh` command is a preflight.
Application Default Credentials are probed first, because the apply needs them
and the installer does not check for them.

The skill also names the namespace the install lands in, which an operator
needs before the first `kubectl` command. The value is the installer's
`DEFAULT_NAMESPACE`, so the test reads it from `install.defaults.env` rather
than repeating it: a changed default fails here instead of leaving the skill
pointing at a namespace the installer no longer uses.

Run:
  python3 -m unittest discover -s tests -p 'test_install_skill_contract.py' -v
"""

from __future__ import annotations

import re
import shlex
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / ".agents/skills/install-kube-agents/SKILL.md"
INSTALL_DEFAULTS = REPO_ROOT / "install.defaults.env"
WORKFLOW_HEADING = "## Install workflow"

# What makes an install.sh run create or mutate nothing: the flags, or the
# environment variables install.sh reads in their place.
DRY_RUN = "--dry-run"
DRY_RUN_ENV = "DRY_RUN=true"
PREFLIGHT_MARKERS = frozenset({DRY_RUN, "--generate-only", DRY_RUN_ENV, "GENERATE_ONLY=true"})
# Namespaces outside the install that a `kubectl` example may legitimately name.
# Anything else must be the installer's DEFAULT_NAMESPACE. Add one here when an
# example needs it.
OTHER_NAMESPACES: frozenset[str] = frozenset()
ADC_PROBE = "gcloud auth application-default print-access-token"

# A fenced block, indented or not (CommonMark allows up to three spaces, and a
# list item's content column), opened by ``` or ~~~ and closed by the same run.
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n(.*?)^[ \t]*\1", re.MULTILINE | re.DOTALL)
INSTALL_SH = re.compile(r"(^|[\s/])install\.sh(\s|$)")
DEFAULT_NAMESPACE = re.compile(r'^DEFAULT_NAMESPACE="([^"]+)"', re.MULTILINE)
KUBECTL_NAMESPACE = re.compile(r"\bkubectl\b[^\n`]*?\s(?:-n|--namespace)[=\s]+([A-Za-z0-9._-]+)")
# install.sh reads its consents and modes from the environment as well as flags
# (ACCEPT_NO_NETWORK_POLICY, ALLOW_UNENCRYPTED_SECRETS, DRY_RUN, ...).
ENV_ASSIGNMENT = re.compile(r"^[A-Z_][A-Z0-9_]*=")
# The probe with its own stdout sent to /dev/null: `>`, `1>`, `&>` or `>>`,
# optionally after a stderr redirect. A redirect on another command on the line,
# or a stderr-only one, still prints the token.
ADC_PROBE_SILENCED = re.compile(
    re.escape(ADC_PROBE) + r"(?:\s+2>(?:&1|\s*/dev/null))*\s+[1&]?>>?\s*/dev/null(?=\s|$)"
)


def install_invocations(text: str) -> list[tuple[int, str]]:
    """Every install.sh command in a fenced block, with its offset in the file.

    Backslash continuations are joined first, so the flags on the lines after
    `install.sh ... \\` count as part of the command they continue.
    """
    found = []
    for block in FENCE.finditer(text):
        joined = re.sub(r"\\\n", " ", block.group(2))
        for command in joined.splitlines():
            if INSTALL_SH.search(command):
                found.append((block.start(), command))
    return found


def tokens(command: str) -> list[str]:
    # Shell words with quotes removed and any `# comment` dropped, so a flag
    # named in a comment does not count as passed.
    return shlex.split(command, comments=True)


def is_preflight(command: str) -> bool:
    return not PREFLIGHT_MARKERS.isdisjoint(tokens(command))


def settings(command: str) -> set[str]:
    # Flags and environment assignments: both set what install.sh does.
    # `bash -s --` ends the curl form's shell options; it is not an installer flag.
    return {
        token
        for token in tokens(command)
        if (token.startswith("--") and token != "--") or ENV_ASSIGNMENT.match(token)
    }


class ParserTest(unittest.TestCase):
    """The shapes the contract tests must not be blind to."""

    def test_indented_fence_is_read(self) -> None:
        text = "1. Decision\n\n   ```bash\n   ./install.sh --non-interactive\n   ```\n"
        self.assertEqual(1, len(install_invocations(text)))

    def test_commented_flag_is_not_a_preflight(self) -> None:
        self.assertFalse(is_preflight("./install.sh --non-interactive  # preview with --dry-run first"))

    def test_env_dry_run_is_a_preflight(self) -> None:
        self.assertTrue(is_preflight("DRY_RUN=true ./install.sh --non-interactive"))

    def test_env_consent_counts_as_a_setting(self) -> None:
        self.assertIn(
            "ACCEPT_NO_NETWORK_POLICY=true",
            settings("ACCEPT_NO_NETWORK_POLICY=true ./install.sh --non-interactive"),
        )

    def test_adc_probe_redirect_must_be_its_own(self) -> None:
        for silenced in (">/dev/null", "1>/dev/null", "&>/dev/null", ">/dev/null 2>&1", "2>/dev/null >/dev/null"):
            self.assertRegex(f'{ADC_PROBE} {silenced} && echo "ADC: ok"', ADC_PROBE_SILENCED, silenced)
        for leaking in ("2>/dev/null", "2>>/dev/null", '&& echo "ADC: ok" >/dev/null'):
            self.assertNotRegex(f"{ADC_PROBE} {leaking}", ADC_PROBE_SILENCED, leaking)


class DryRunPrecedesApplyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SKILL.read_text(encoding="utf-8")
        self.invocations = install_invocations(self.text)

    def test_adc_is_probed_before_the_first_install_command(self) -> None:
        # The apply's Terraform needs Application Default Credentials, and the
        # installer's non-interactive auth check tests only gcloud user
        # credentials. On an existing cluster the irreversible adoption changes
        # run before Terraform, so a missing ADC fails the install after them.
        # The probe must not print the token into the agent's transcript.
        before = self.text[: self.invocations[0][0]]
        probes = [line for line in before.splitlines() if ADC_PROBE in line]
        self.assertTrue(probes, f"probe ADC (`{ADC_PROBE}`) before the first install.sh command")
        for probe in probes:
            self.assertRegex(probe, ADC_PROBE_SILENCED, "the ADC probe prints the access token")

    def test_first_install_command_is_a_preflight(self) -> None:
        offset, command = self.invocations[0]
        line = self.text.count("\n", 0, offset) + 1
        self.assertTrue(
            is_preflight(command),
            f"SKILL.md line {line}: the first install.sh command a reader meets "
            f"applies ({command.strip()!r}); show the --dry-run first",
        )

    def workflow_bounds(self) -> tuple[int, int]:
        start = self.text.index(WORKFLOW_HEADING)
        end = self.text.find("\n## ", start + len(WORKFLOW_HEADING))
        return start, end

    def test_every_workflow_apply_repeats_the_dry_run_flags(self) -> None:
        # The skill tells the agent to apply "the last stage 2 command without
        # --dry-run", and shows that apply three ways (curl, release bundle,
        # checkout). Hand-written copies drift; an agent copying one that did
        # would apply flags the operator never previewed. Flags and environment
        # assignments are compared, not the rest of the command, since the three
        # forms invoke install.sh differently.
        start, end = self.workflow_bounds()
        commands = [c for o, c in self.invocations if start <= o < end]
        dry_run = next(c for c in commands if {DRY_RUN, DRY_RUN_ENV} & set(tokens(c)))
        applies = [c for c in commands if not is_preflight(c)]
        self.assertTrue(applies, f"no apply under {WORKFLOW_HEADING!r}")
        expected = sorted(settings(dry_run) - {DRY_RUN, DRY_RUN_ENV})
        for apply in applies:
            self.assertEqual(expected, sorted(settings(apply)), apply.strip())

    def test_no_apply_outside_the_workflow(self) -> None:
        # An agent lifts fenced commands out of any section, not only the
        # workflow. Every apply lives in the workflow, where the test above
        # ties it to the dry run; a command elsewhere is a preflight.
        start, end = self.workflow_bounds()
        for offset, command in self.invocations:
            if start <= offset < end:
                continue
            line = self.text.count("\n", 0, offset) + 1
            self.assertTrue(
                is_preflight(command),
                f"SKILL.md line {line}: install.sh applies outside {WORKFLOW_HEADING!r} "
                f"({command.strip()!r}); show it with --dry-run",
            )


class NamespaceMatchesInstallerDefaultTest(unittest.TestCase):
    def setUp(self) -> None:
        match = DEFAULT_NAMESPACE.search(INSTALL_DEFAULTS.read_text(encoding="utf-8"))
        self.assertIsNotNone(match, "install.defaults.env carries no DEFAULT_NAMESPACE")
        self.namespace = match.group(1)
        self.text = SKILL.read_text(encoding="utf-8")

    def test_namespace_is_named_before_the_first_install_command(self) -> None:
        first_command = install_invocations(self.text)[0][0]
        self.assertTrue(
            f"`{self.namespace}`" in self.text[:first_command],
            f"name `{self.namespace}` before the first install.sh command",
        )

    def test_every_kubectl_namespace_is_the_default(self) -> None:
        # Any namespace not listed in OTHER_NAMESPACES is taken to mean the
        # install's, so a stale or misspelt one (`kube-agents`) fails here.
        namespaces = set(KUBECTL_NAMESPACE.findall(self.text)) - OTHER_NAMESPACES
        self.assertTrue(namespaces, "SKILL.md shows no `kubectl ... -n` into the install")
        self.assertEqual({self.namespace}, namespaces)


if __name__ == "__main__":
    unittest.main()
