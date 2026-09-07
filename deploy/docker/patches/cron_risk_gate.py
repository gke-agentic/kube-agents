"""Security gates for cron runs: read-only policy, code execution refuse, and
content checks.

Installed into the image at ``/opt/hermes/tools/cron_risk_gate.py`` and wired
into ``tools/approval.py`` by ``deploy/docker/Dockerfile``.

Addresses Issue #993 (THREAT-002, SKILL-002):
1. Terminal escape and control character injection (_ESC pattern).
2. Pure-ASCII lookalike TLD / domain evasion (e.g. kubernetes.io.evil-cdn.co).
3. Unconditional block on execute_code in autonomous cron runs.
4. Per-job risk tiers: 'high' applies a fail-closed read-only command policy
   (allowlist of inspection verbs); 'low' retains the denylist-only floor.

Gates 1-3 are unconditional on every cron run. ``approvals.cron_scan`` opts out
of the Tirith content scan only (see cron_tirith_scan.py), never these.
"""

from __future__ import annotations

import logging
import re
import shlex
from typing import Callable, Optional

logger = logging.getLogger(__name__)

RISK_LOW = "low"
RISK_HIGH = "high"

CRON_SCAN_KEY = "cron_scan"
APPROVALS_KEY = "approvals"

MAX_LOG_COMMAND_LEN = 200

MSG_EXECUTE_CODE_REFUSED = (
    "BLOCKED: execute_code is refused during autonomous cron runs "
    "(THREAT-002). Autonomous watchdogs may not execute raw code."
)
MSG_ESC_REFUSED = (
    "BLOCKED: command contains raw terminal escape or control characters "
    "(THREAT-002). Terminal escape injection is refused during cron runs."
)
MSG_LOOKALIKE_TEMPLATE = (
    "BLOCKED: command contains lookalike domain '{host}' mimicking trusted apex '{apex}' "
    "(THREAT-002). Lookalike domain evasion is refused during cron runs."
)
MSG_MUTATION_REFUSED = (
    "BLOCKED: command is not a recognized read-only inspection command and this "
    "cron job is classified read-only (risk=high, SKILL-002). Only allowlisted "
    "read commands run; the audit continues."
)

#: Characters that alter terminal state or conceal command strings:
#: C0 control characters (excluding newline \n, tab \t, carriage return \r),
#: DEL (\x7f), and C1 control characters (\x80-\x9f, including 8-bit CSI \x9b).
_ESC = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f]")

#: Apex domains trusted for Kubernetes and GKE platform operations.
TRUSTED_APEX = (
    "kubernetes.io",
    "googleapis.com",
    "github.com",
    "githubusercontent.com",
    "k8s.io",
    "x-k8s.io",
    "google.com",
    "gke.io",
)

#: Extracts hostname candidates from URLs, CLI flags (--server=...), @hosts, quotes, or tokens.
#: Uses fixed-width lookbehinds so chained delimiters (e.g. comma, semicolon, pipes, brackets)
#: are recognized without prematurely consuming the separator.
_HOST_TOKEN = re.compile(
    r"(?:https?://|--[a-z0-9_-]+=|(?<=^)|(?<=[\s@'\"=,;|([{`]))([a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+)",
    re.IGNORECASE,
)

#: Executables that are read-only in every invocation (text/inspection utils).
_READ_ONLY_TOOLS = frozenset({
    "grep", "egrep", "fgrep", "awk", "jq", "yq", "cut", "sort", "uniq", "head",
    "tail", "wc", "cat", "tr", "column", "nl", "comm", "join", "paste", "fold",
    "rev", "echo", "printf", "date", "hostname", "pwd", "true", "test",
})

#: Wrappers / interpreters whose presence makes a segment unanalyzable -> refuse.
_INDIRECTION = frozenset({
    "sh", "bash", "zsh", "ash", "dash", "ksh", "python", "python3", "perl",
    "ruby", "node", "eval", "exec", "env", "xargs", "watch", "timeout", "nohup",
    "nice", "ssh", "sudo", "su", "find", "flock", "setsid", "stdbuf", "script",
})

#: Subcommand tools: any mutating verb refuses; else a read verb is required.
_TOOL_MUTATE_VERBS = {
    "kubectl": {
        "create", "apply", "delete", "patch", "edit", "replace", "scale",
        "autoscale", "annotate", "label", "set", "rollout", "drain", "cordon",
        "uncordon", "taint", "exec", "cp", "attach", "port-forward", "proxy",
        "run", "expose", "rollback", "wait", "debug", "set-context",
        "set-cluster", "set-credentials", "use-context",
    },
    "oc": {"create", "apply", "delete", "patch", "edit", "replace", "scale",
           "rollout", "set", "adm"},
    "gcloud": {"create", "delete", "update", "set", "add", "remove", "enable",
               "disable", "reset", "resize", "patch", "import", "deploy",
               "rollback", "restart", "attach", "detach", "clear", "replace",
               "abandon", "cancel", "start", "stop", "suspend", "resume"},
    "gsutil": {"cp", "mv", "rm", "rsync", "mb", "rb", "setmeta", "acl", "iam"},
    "gh": {"create", "delete", "edit", "close", "merge", "comment", "clone"},
    "helm": {"install", "upgrade", "uninstall", "rollback", "delete"},
    "bq": {"insert", "mk", "rm", "update", "cp", "load"},
}
_TOOL_READ_VERBS = {
    "kubectl": {"get", "describe", "logs", "top", "explain", "version",
                "api-resources", "api-versions", "cluster-info", "events",
                "diff", "auth", "config", "can-i", "whoami"},
    "oc": {"get", "describe", "logs", "status", "whoami"},
    "gcloud": {"list", "describe", "info", "version", "get-iam-policy", "search"},
    "gsutil": {"ls", "stat", "cat", "du", "hash", "ver", "version"},
    "gh": {"view", "list", "status"},
    "helm": {"list", "get", "status", "history", "show", "search", "version"},
    "bq": {"ls", "show", "head", "query"},
}

#: Read-only local writes tolerated in a read-only run.
_REDIR_OK_TARGETS = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr"})

_SEP = re.compile(r"\|\||&&|[;\n|]")           # segment separators
_SUBST = re.compile(r"\$\(|`|<\(|>\(")          # command / process substitution
_REDIR = re.compile(r"^(\d*)>>?(&?)(.*)$")      # 1>foo, 2>>bar, 2>&1, >&2


def _load_config_readonly() -> dict:
    """Read config.yaml without taking a write lock, or ``{}``.

    Deferred import so importing approval.py at startup does not load configuration early.
    """
    try:
        from hermes_cli.config import load_config_readonly

        return load_config_readonly() or {}
    except Exception:
        return {}


def cron_scan_enabled(config: Optional[dict]) -> bool:
    """Whether ``approvals.cron_scan`` leaves the scan on. Default: yes.

    Anything other than an explicit false-y value keeps the scan, matching the
    opt-out contract in cron_tirith_scan.py.
    """
    approvals = (config or {}).get(APPROVALS_KEY)
    if not isinstance(approvals, dict):
        return True
    return bool(approvals.get(CRON_SCAN_KEY, True))


def _redirect_ok(tokens: list[str]) -> bool:
    """True unless a segment writes anywhere but /dev/null or an fd dup."""
    for i, tok in enumerate(tokens):
        m = _REDIR.match(tok)
        if not m or (not m.group(2) and not tok[:1].isdigit() and ">" not in tok):
            continue
        if not (">" in tok or tok.endswith(">")):
            continue
        if m.group(2) == "&":                    # 2>&1, >&2
            continue
        target = m.group(3) or (tokens[i + 1] if i + 1 < len(tokens) else "")
        if target not in _REDIR_OK_TARGETS:
            return False
    return True


def _segment_is_read_only(segment: str) -> bool:
    """Classify one shell segment. Unknown or unanalyzable -> False (fail closed)."""
    if _SUBST.search(segment):
        return False
    try:
        tokens = shlex.split(segment, comments=False, posix=True)
    except ValueError:
        return False                             # unbalanced quotes -> refuse
    if not tokens:
        return True                              # empty segment (e.g. trailing sep)
    while tokens and "=" in tokens[0] and not tokens[0].startswith(("-", "/")):
        tokens = tokens[1:]                       # strip leading VAR=value assignments
    if not tokens:
        return False
    if not _redirect_ok(tokens):
        return False

    exe = tokens[0].rsplit("/", 1)[-1]
    if "$" in exe or exe.startswith("-"):
        return False
    if exe in _INDIRECTION:
        return False
    if exe in _READ_ONLY_TOOLS:
        return True

    read = _TOOL_READ_VERBS.get(exe)
    mutate = _TOOL_MUTATE_VERBS.get(exe)
    if read is None:
        return False                             # unknown executable -> refuse

    rest = tokens[1:]
    if exe in ("kubectl", "oc") and any(
        t == "--dry-run=client" or t == "--dry-run=server" or t.startswith("--dry-run=c")
        or t.startswith("--dry-run=s") for t in rest
    ):
        return True                              # server/client dry-run is read-only
    positionals = [t for t in rest if not t.startswith("-") and "=" not in t]
    if any(t in mutate for t in positionals):
        return False
    if any(t in read for t in positionals):
        return True
    return False                                 # no recognized read verb -> refuse


def cron_command_policy_block(command: str, risk: str | None) -> Optional[dict]:
    """Refuse anything not provably read-only when the job is 'high' risk.

    'low' keeps the denylist-only floor (returns None). 'high' (and the
    fail-closed unannotated default) requires every shell segment to be an
    allowlisted read-only command; unknown, mutating, or unanalyzable commands
    are refused while the run continues.
    """
    if (risk or RISK_HIGH).strip().lower() == RISK_LOW:
        return None
    if not command or not isinstance(command, str):
        return None
    for segment in _SEP.split(command):
        if segment.strip() and not _segment_is_read_only(segment):
            logger.warning(
                "Cron risk gate block [read-only]: refused non-read segment (command: %s)",
                command[:MAX_LOG_COMMAND_LEN],
            )
            return {"approved": False, "message": MSG_MUTATION_REFUSED}
    return None


def cron_execute_code_block() -> Optional[dict]:
    """Refuse execute_code unconditionally during autonomous cron runs.

    Autonomous watchdogs have no human operator present and must perform their
    actions using declared tools and read-only commands rather than running
    arbitrary embedded scripts.
    """
    logger.warning("Cron risk gate block [execute_code]: %s", MSG_EXECUTE_CODE_REFUSED)
    return {
        "approved": False,
        "message": MSG_EXECUTE_CODE_REFUSED,
    }


def find_lookalike_domain(command: str) -> Optional[tuple[str, str]]:
    """Detect whether any host token in the command mimics a trusted apex domain.

    Returns (detected_host, matched_apex) if a lookalike is detected, else None.
    Legitimate exact matches (e.g. 'k8s.io') and proper subdomains (e.g.
    'storage.googleapis.com', 'raw.githubusercontent.com', 'kubeagents.x-k8s.io')
    pass cleanly.
    """
    if not command or not isinstance(command, str):
        return None

    for match in _HOST_TOKEN.finditer(command):
        raw = match.group(1).lower().rstrip(".:/'\"")
        for apex in TRUSTED_APEX:
            # Legitimate apex or proper subdomain of apex
            if raw == apex or raw.endswith("." + apex):
                continue
            # Lookalike evasion: token contains the apex at a dot/label boundary
            # (e.g. 'kubernetes.io.evil-cdn.co' or 'sub.kubernetes.io.attacker.com')
            # rather than terminating at the apex.
            if raw.startswith(apex + ".") or ("." + apex + ".") in raw:
                return raw, apex
    return None


def cron_content_block(
    command: str,
    *,
    load_config: Optional[Callable[[], dict]] = None,
) -> Optional[dict]:
    """Refuse escape/control-char injection and lookalike-domain evasion.

    Unconditional on every cron run: ``approvals.cron_scan`` gates the Tirith
    content scan only, not these THREAT-002 evasion classes. ``load_config`` is
    accepted for backward-compatible call sites and ignored.
    """
    if not command or not isinstance(command, str):
        return None

    if _ESC.search(command):
        logger.warning(
            "Cron risk gate block [escape]: command contains raw control/escape characters (command: %s)",
            command[:MAX_LOG_COMMAND_LEN],
        )
        return {
            "approved": False,
            "message": MSG_ESC_REFUSED,
        }

    lookalike = find_lookalike_domain(command)
    if lookalike is not None:
        host, apex = lookalike
        logger.warning(
            "Cron risk gate block [lookalike]: command contains lookalike domain '%s' mimicking apex '%s' (command: %s)",
            host,
            apex,
            command[:MAX_LOG_COMMAND_LEN],
        )
        return {
            "approved": False,
            "message": MSG_LOOKALIKE_TEMPLATE.format(host=host, apex=apex),
        }

    return None
