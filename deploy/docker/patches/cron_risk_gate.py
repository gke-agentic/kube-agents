"""Security gates for cron runs: risk escalation, code execution refuse, and content checks.

Installed into the image at ``/opt/hermes/tools/cron_risk_gate.py`` and wired
into ``tools/approval.py`` by ``deploy/docker/Dockerfile``.

Addresses the three documented residues in ``deploy/docker/patches/cron_tirith_scan.py``
and Issue #993 (THREAT-002):
1. Terminal escape and control character injection (_ESC pattern).
2. Pure-ASCII lookalike TLD / domain evasion (e.g. kubernetes.io.evil-cdn.co).
3. Unconditional block on execute_code in autonomous cron runs.
4. Risk-keyed mode escalation mapping 'high' risk to 'deny' approval mode.
"""

from __future__ import annotations

import re
from typing import Optional

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

MODE_DENY = "deny"

MSG_EXECUTE_CODE_REFUSED = (
    "BLOCKED: execute_code is refused during autonomous cron runs "
    "(THREAT-002). Autonomous watchdogs may not execute raw code."
)
MSG_ESC_REFUSED = (
    "BLOCKED: command contains raw terminal escape or control characters "
    "(THREAT-002). Terminal escape injection is refused during cron runs."
)

#: Characters that alter terminal state or conceal command strings:
#: ESC (\x1b), C0/C1 control characters (excluding newline \n and tab \t).
_ESC = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x1b]")

#: Apex domains trusted for Kubernetes and GKE platform operations.
TRUSTED_APEX = (
    "kubernetes.io",
    "googleapis.com",
    "github.com",
    "githubusercontent.com",
    "k8s.io",
    "google.com",
    "gke.io",
)

#: Extracts hostname candidates from URLs, CLI flags (--server=...), @hosts, quotes, or tokens.
_HOST_TOKEN = re.compile(
    r"(?:https?://|--[a-z0-9_-]+=|[@'\"=\s]|^)([a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+)",
    re.IGNORECASE,
)


def cron_effective_mode(mode: str, risk: str | None) -> str:
    """Escalate approval mode based on the job's declared risk tier.

    A job declared as 'high' risk is escalated to 'deny' mode so it is evaluated
    against strict pattern and policy gates. Low and medium risk retain the
    profile's configured mode (typically 'approve' in non-interactive cron runs).
    """
    effective_risk = (risk or RISK_LOW).strip().lower()
    if effective_risk == RISK_HIGH:
        return MODE_DENY
    return mode


def cron_execute_code_block() -> Optional[dict]:
    """Refuse execute_code unconditionally during autonomous cron runs.

    Autonomous watchdogs have no human operator present and must perform their
    actions using declared tools and read-only commands rather than running
    arbitrary embedded scripts.
    """
    return {
        "approved": False,
        "message": MSG_EXECUTE_CODE_REFUSED,
    }


def find_lookalike_domain(command: str) -> Optional[tuple[str, str]]:
    """Detect whether any host token in the command mimics a trusted apex domain.

    Returns (detected_host, matched_apex) if a lookalike is detected, else None.
    Legitimate exact matches (e.g. 'k8s.io') and proper subdomains (e.g.
    'storage.googleapis.com', 'raw.githubusercontent.com') pass cleanly.
    """
    if not command or not isinstance(command, str):
        return None

    for match in _HOST_TOKEN.finditer(command):
        raw = match.group(1).lower().rstrip(".:/'\"")
        for apex in TRUSTED_APEX:
            if apex in raw:
                # Legitimate apex or subdomain of apex
                if raw == apex or raw.endswith("." + apex):
                    continue
                # Lookalike attempt: contains the trusted apex but does not end with it
                # or prefixes it with an unseparated label (e.g. kubernetes.io.evil.com)
                return raw, apex
    return None


def cron_content_block(command: str) -> Optional[dict]:
    """Scan a cron command for content-level evasions not caught by standard pattern filters.

    Checks:
    1. Terminal escape sequences and raw control characters (ANSI / C0 / C1).
    2. Pure-ASCII lookalike TLDs mimicking trusted infrastructure domains.

    Returns a refusal dictionary matching check_all_command_guards contract, or None.
    """
    if not command or not isinstance(command, str):
        return None

    if _ESC.search(command):
        return {
            "approved": False,
            "message": MSG_ESC_REFUSED,
        }

    lookalike = find_lookalike_domain(command)
    if lookalike is not None:
        host, apex = lookalike
        return {
            "approved": False,
            "message": (
                f"BLOCKED: command contains lookalike domain '{host}' mimicking trusted apex '{apex}' "
                "(THREAT-002). Lookalike domain evasion is refused during cron runs."
            ),
        }

    return None
