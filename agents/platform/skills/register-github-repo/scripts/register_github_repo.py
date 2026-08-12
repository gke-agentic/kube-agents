#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys

# Append scripts paths to allow importing platform utilities
sys.path.append(os.path.join(os.path.dirname(__file__), "../../../scripts"))
sys.path.append("/opt/defaults/scripts")
sys.path.append("/opt/data/scripts")

from github_token_refresh import refresh_git_credentials
from gitops_workspace import get_managed_repos


def run(cmd, **kwargs):
    return subprocess.run(cmd, text=True, **kwargs)


def register_repo(repo: str) -> int:
    repo = repo.strip()
    if not repo or repo.count("/") != 1 or any(part == "" for part in repo.split("/")):
        print(
            "Error: Invalid repository format. Please provide a valid 'owner/repo' string.",
            file=sys.stderr,
        )
        return 1

    print(f"Minting new Github Token for {repo}...")
    try:
        refresh_git_credentials(repo)
    except Exception as e:
        print(
            f"Error: Failed to mint token for {repo}. Ensure Token Broker has access. ({e})",
            file=sys.stderr,
        )
        return 1

    print(f"Verifying access to {repo}...")
    try:
        run(["gh", "repo", "view", repo, "--json", "id"], capture_output=True, check=True)
    except FileNotFoundError:
        print("Error: 'gh' CLI tool not found in PATH.", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError:
        print(
            f"Error: Agent's GitHub App does not have access to {repo}. Please install the GitHub app on the repository.",
            file=sys.stderr,
        )
        return 1

    cfg_name = os.environ.get("GITHUB_STATE_CONFIGMAP", "platform-agent-github-state")
    ns = os.environ.get("KUBE_DEFAULT_NAMESPACE", "kubeagents-system")

    print(f"Access verified. Patching ConfigMap {cfg_name}...")

    try:
        repos = get_managed_repos()
    except RuntimeError as e:
        print(f"Error reading ConfigMap: {e}", file=sys.stderr)
        return 1

    if repo in repos:
        print(f"Repository {repo} is already in the managed list.")
        return 0

    repos.append(repo)
    new_repos_str = ", ".join(repos)

    patch = {"data": {"managed_repos": new_repos_str}}
    try:
        run(
            ["kubectl", "patch", "configmap", cfg_name, "-n", ns, "--type=merge", "-p", json.dumps(patch)],
            capture_output=True,
            check=True,
        )
        print(f"Successfully added {repo} to {cfg_name}.")
        return 0
    except FileNotFoundError:
        print("Error: 'kubectl' binary not found in PATH.", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to update ConfigMap: {e.stderr or e}", file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(description="Verify and register a new GitHub repo to the agent ConfigMap.")
    parser.add_argument("--repo", required=True, help="Repository in owner/repo format")
    args = parser.parse_args()

    sys.exit(register_repo(args.repo))


if __name__ == "__main__":
    main()
