#!/usr/bin/env python3
"""
resolver.py — Deterministic helper script for the github-issue-resolver skill.
Encapsulates GitHub CLI (gh) operations, label management, stale issue sweeps,
and safe report uploading via standard subprocess execution.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# The shared scripts dir holds github_token_refresh (docker-entrypoint.sh keeps
# executable scripts shared across profiles rather than copying them into each
# one). The import itself is lazy, in refresh_credentials below, so this module
# still imports on a dev machine with nothing staged under /opt. The third entry
# is the same directory in a source checkout. Mirrors fleet-audit's audit_report,
# which needs the same module for the same reason.
sys.path.append("/opt/defaults/scripts")
sys.path.append("/opt/data/scripts")
sys.path.append(str(Path(__file__).resolve().parents[3] / "scripts"))

from gitops_workspace import get_managed_repos

SCRATCH_DIR = "/opt/data/scratch"

# Shell convention for "command not found", reused so a missing binary stays
# distinguishable from a gh command that ran and failed.
GH_MISSING_RC = 127

# The credential sidecar's own timeout (`_execute` in credential_proxy.py),
# surfaced through credential_proxy_client. Excluded from the retry because a
# command that ran for the full timeout may well have landed its write; see
# _looks_like_auth_failure.
GH_TIMEOUT_RC = 124

# What `gh` prints when the credential is the problem, as opposed to the
# repository, the network, or the rate limit. Matched case-insensitively
# against stderr: the REST paths emit `HTTP 401: Bad credentials`, the GraphQL
# ones `requires authentication`, and `auth status` (which is handled
# separately, being the explicit question) `not logged in` / `token is invalid`.
_GH_AUTH_FAILURE = re.compile(
    r"HTTP 401"
    r"|bad credentials"
    r"|requires authentication"
    r"|authentication failed"
    r"|not logged in"
    r"|token is invalid"
    r"|invalid token",
    re.IGNORECASE,
)

# Per-process credential-refresh state, owned by _refresh_credentials_once.
# `_attempted` bounds an invocation to a single mint; `_failed` lets handle_poll
# tell "the broker refused" apart from "nobody configured credentials", which
# need different operators. Tests reset both.
_refresh_attempted = False
_refresh_failed = False

# The operator accepts a bare "owner/repo" shorthand as a valid gitRepo.
# Mirrors ownerRepoRegex in k8s-operator/api/v1alpha1/common_types.go.
BARE_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

def _run_gh_once(args: list) -> subprocess.CompletedProcess:
    """Run one gh command, mapping a missing binary onto a return code.

    Never raises, so :func:`run_gh` can inspect a failure and decide whether it
    is worth retrying before applying the caller's ``check`` semantics.
    """
    try:
        return subprocess.run(
            ["gh"] + args, check=False, text=True, capture_output=True
        )
    except FileNotFoundError:
        # Distinguishable from a gh command that ran and failed, so callers can
        # name the fault precisely.
        return subprocess.CompletedProcess(
            ["gh"] + args,
            GH_MISSING_RC,
            stdout="",
            stderr="'gh' CLI binary not found in PATH.",
        )


def _looks_like_auth_failure(args: list, result) -> bool:
    """Does this failure look like one a fresh token would fix?"""
    if result.returncode == 0:
        return False
    if result.returncode in (GH_MISSING_RC, GH_TIMEOUT_RC):
        return False
    if args[:2] == ["auth", "status"]:
        return True
    return bool(_GH_AUTH_FAILURE.search(result.stderr or ""))


def _refresh_credentials_once(args: list = None) -> bool:
    """Mint a fresh token, at most once per process.

    Returns True only when a new token actually landed -- i.e. when retrying
    the gh command that just failed is worth doing.
    """
    global _refresh_attempted, _refresh_failed
    if _refresh_attempted:
        return False
    _refresh_attempted = True

    repo = None
    if args and "-R" in args:
        try:
            repo = args[args.index("-R") + 1]
        except (ValueError, IndexError):
            pass

    if not repo:
        try:
            managed = get_managed_repos()
            repo = managed[0] if managed else None
        except Exception:
            repo = None

    if not repo:
        return False

    try:
        refresh_credentials(repo)
    except Exception as exc:
        print(
            f"resolver: GitHub credential refresh failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        _refresh_failed = True
        return False
    return True


def run_gh(args: list, check: bool = True) -> subprocess.CompletedProcess:
    """Runs a gh CLI command safely without shell escaping or ampersand backgrounding issues.

    A failed call gets one retry behind a freshly minted token.
    """
    result = _run_gh_once(args)
    if _looks_like_auth_failure(args, result) and _refresh_credentials_once(args):
        result = _run_gh_once(args)

    if check and result.returncode != 0:
        if result.returncode == GH_MISSING_RC:
            print("Error: 'gh' CLI binary not found in PATH.", file=sys.stderr)
        else:
            print(
                f"Error running gh command: {' '.join(args)}\n{result.stderr}",
                file=sys.stderr,
            )
        sys.exit(result.returncode)
    return result


def refresh_credentials(repo: str) -> None:
    from github_token_refresh import refresh_git_credentials
    refresh_git_credentials(repo)


def ensure_labels_exist(repo: str):
    labels = [
        ("status:in-progress", "FBCA04", "Currently being actively investigated by the Platform Agent"),
        ("status:resolved", "0E8A16", "Issue resolved autonomously by Platform Agent"),
        ("status:escalation-needed", "B60205", "Issue requires human review/SRE action"),
        ("agent:ignore", "E99695", "Permanently ignored by automated issue resolvers"),
    ]
    for name, color, desc in labels:
        run_gh([
            "label", "create", name, "-R", repo, "--color", color, "--description", desc, "--force",
        ], check=False)


def sweep_stale_issues(repo: str):
    res = run_gh([
        "issue", "list", "-R", repo, "--label", "status:in-progress", "--json", "number,title,updatedAt",
    ], check=False)
    if res.returncode != 0:
        return

    try:
        issues = json.loads(res.stdout)
        if not isinstance(issues, list):
            issues = []
    except Exception:
        issues = []

    now = datetime.datetime.now(datetime.timezone.utc)
    stale_msg = (
        "🚨 **Autonomous Investigation Timed Out — Human Escalation Required**\n\n"
        "The Platform Agent previously claimed this issue (`status:in-progress`) but no updates were "
        "recorded within the 2-hour SLA window (stale investigation/crash). Transitioning to human review."
    )

    for i in issues:
        updated_str = i.get("updatedAt")
        if not updated_str:
            continue
        try:
            updated = datetime.datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
            if (now - updated).total_seconds() > 7200:
                num = str(i["number"])
                run_gh(["issue", "comment", num, "-R", repo, "--body", stale_msg], check=False)
                run_gh(["issue", "edit", num, "-R", repo, "--add-label", "status:escalation-needed", "--remove-label", "status:in-progress"], check=False)
        except Exception:
            continue


def handle_poll(args):
    try:
        repos = get_managed_repos()
    except Exception as e:
        print(json.dumps({"status": "ERROR", "reason": "CONFIGMAP_READ_FAILED", "error": str(e)}))
        return

    repos = [r for r in repos if BARE_REPO_RE.match(r)]
    if not repos:
        print(json.dumps({"status": "NOT_CONFIGURED"}))
        return

    # Check auth pre-flight safely. A repo is configured but credentials are
    # broken: that is a real fault, so it must NOT be reported as NO_ISSUES
    # (which the skill silences) or the resolver goes quiet forever.
    #
    # A failed pre-flight is not yet evidence of that fault. The credential it
    # fails on is short-lived by construction -- the GitHub App installation
    # token the broker mints expires after an hour, while this poller runs every
    # ten minutes -- so an expired token is the expected steady state between
    # refreshes. run_gh mints once and retries, so by the time this returns
    # non-zero a *freshly minted* token was also rejected. Before that retry
    # existed, an ordinary expiry was reported as GITHUB_AUTH_NOT_CONFIGURED,
    # which sent operators hunting for configuration that was already correct
    # while the watcher stayed silent about real issues for the rest of the
    # token's life.
    auth = run_gh(["auth", "status"], check=False)

    if auth.returncode != 0:
        # Three faults, three operators, three reason codes -- collapsing them
        # is the conflation that made this failure unreadable to begin with.
        # A broker that refused is not a missing binary and neither is a
        # credential nobody ever configured.
        if _refresh_failed:
            reason = "GITHUB_TOKEN_REFRESH_FAILED"
        elif auth.returncode == GH_MISSING_RC:
            reason = "GH_CLI_NOT_FOUND"
        else:
            reason = "GITHUB_AUTH_NOT_CONFIGURED"
        print(json.dumps({"status": "ERROR", "reason": reason}))
        return

    all_issues = []
    unreachable_repos = []

    for repo in repos:
        sweep_stale_issues(repo)

        search_query = "is:issue is:open -label:status:in-progress -label:status:escalation-needed -label:agent:ignore -label:status:resolved -label:agent:audit"
        res = run_gh([
            "issue", "list", "-R", repo, "--search", search_query, "--json", "number,title,body,comments,createdAt", "--limit", "10",
        ], check=False)
        if res.returncode != 0:
            unreachable_repos.append(repo)
            continue

        try:
            issues = json.loads(res.stdout)
            if not isinstance(issues, list):
                unreachable_repos.append(repo)
                continue
        except Exception:
            unreachable_repos.append(repo)
            continue

        for issue in issues:
            issue["_repo"] = repo
            all_issues.append(issue)

    if not all_issues:
        if unreachable_repos and len(unreachable_repos) == len(repos):
            print(json.dumps({"status": "ERROR", "reason": "REPO_UNREACHABLE", "unreachable_repos": unreachable_repos}))
            return
        print(json.dumps({"status": "NO_ISSUES", "managed_repos": repos, "unreachable_repos": unreachable_repos}))
        return

    all_issues.sort(key=lambda x: (x.get("createdAt", ""), int(x["number"])))
    target = all_issues[0]
    repo = target["_repo"]
    comments = []
    for c in target.get("comments", []):
        author = c.get("author", {}).get("login", "unknown")
        body = c.get("body", "")
        created = c.get("createdAt", "")
        comments.append({"author": author, "createdAt": created, "body": body})

    print(
        json.dumps(
            {
                "status": "FOUND",
                "repository": repo,
                "issue_number": target["number"],
                "title": target["title"],
                "body": target.get("body", ""),
                "comments": comments,
                "unreachable_repos": unreachable_repos,
            },
            indent=2,
        )
    )

def _validate_repo_or_exit(repo: str) -> None:
    if not repo or not BARE_REPO_RE.match(repo):
        print(json.dumps({"status": "ERROR", "reason": "INVALID_REPOSITORY", "error": f"Invalid repository format: {repo!r}"}))
        sys.exit(1)
    try:
        managed = get_managed_repos()
    except Exception as e:
        print(json.dumps({"status": "ERROR", "reason": "CONFIGMAP_READ_FAILED", "error": str(e)}))
        sys.exit(1)
    if repo not in managed:
        print(json.dumps({"status": "ERROR", "reason": "UNMANAGED_REPOSITORY", "error": f"Repository {repo!r} is not in the managed repositories list: {managed}"}))
        sys.exit(1)

def handle_claim(args):
    repo = args.repo
    _validate_repo_or_exit(repo)
    issue_num = str(args.issue)
    ensure_labels_exist(repo)

    run_gh(["issue", "edit", issue_num, "-R", repo, "--add-label", "status:in-progress"])
    claim_msg = "🤖 **Platform Agent Triaging:** Issue marked `status:in-progress`. Beginning root cause investigation and recording worklog..."
    run_gh(["issue", "comment", issue_num, "-R", repo, "--body", claim_msg])

    print(json.dumps({"status": "CLAIMED", "issue_number": int(issue_num), "repository": repo}, indent=2))

def handle_transition(args):
    repo = args.repo
    issue_num = str(args.issue)
    state = args.state
    report_file = args.report_file

    scratch_dir = os.path.realpath(SCRATCH_DIR)
    real_report_path = os.path.realpath(report_file)
    if not real_report_path.startswith(scratch_dir + os.sep):
        print(f"Error: Report file {report_file} resolves outside {scratch_dir}.", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(real_report_path):
        print(f"Error: Report file {report_file} does not exist.", file=sys.stderr)
        sys.exit(1)

    _validate_repo_or_exit(repo)

    run_gh(["issue", "comment", issue_num, "-R", repo, "-F", real_report_path])
    run_gh(["issue", "edit", issue_num, "-R", repo, "--add-label", f"status:{state}", "--remove-label", "status:in-progress"])

    if state == "resolved":
        run_gh(["issue", "close", issue_num, "-R", repo, "--reason", "completed"])

    try:
        os.remove(real_report_path)
    except Exception:
        pass

    print(json.dumps({"status": "TRANSITIONED", "issue_number": int(issue_num), "new_state": state, "repository": repo}, indent=2))

def main():
    parser = argparse.ArgumentParser(description="Deterministic GitHub issue resolver helper.")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    subparsers.add_parser("poll", help="Poll unaddressed issues and sweep stale investigations.")

    claim_parser = subparsers.add_parser("claim", help="Claim an open issue.")
    claim_parser.add_argument("--issue", required=True, type=int, help="Issue number to claim.")
    claim_parser.add_argument("--repo", required=True, help="Target repository to act upon.")

    trans_parser = subparsers.add_parser("transition", help="Upload report and transition issue label/state.")
    trans_parser.add_argument("--issue", required=True, type=int, help="Issue number to transition.")
    trans_parser.add_argument("--repo", required=True, help="Target repository to act upon.")
    trans_parser.add_argument("--state", required=True, choices=["resolved", "escalation-needed"], help="New state label.")
    trans_parser.add_argument("--report-file", required=True, help="Path to markdown report file to post as comment.")

    args = parser.parse_args()
    if args.subcommand == "poll":
        handle_poll(args)
    elif args.subcommand == "claim":
        handle_claim(args)
    elif args.subcommand == "transition":
        handle_transition(args)

if __name__ == "__main__":
    main()
