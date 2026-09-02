#!/usr/bin/env python3
"""Assert DNS egress rule parity across static NetworkPolicy copies.

Several static files hand-maintain the same DNS egress rule (port 53):
* charts/kube-agents/templates/litellm.yaml
* charts/kube-agents/templates/github-minter.yaml
* deploy/kustomize/platform/networkpolicy-core-egress.yaml
* examples/litellm-chatgpt-subscription/networkpolicy.yaml
* examples/litellm-gemini/networkpolicy.yaml
* examples/vllm-gemma/networkpolicy.yaml
* k8s-operator/config/integrations/github/deployment.yaml.template
* k8s-operator/config/integrations/litellm/base/networkpolicy.yaml

Context (#747 B5, D1; #687):
#687 had to touch all static copies, and its first draft missed one —
deployment.yaml.template, because greps for *.yaml skip it. Furthermore, #608
added a static policy that omitted the required DNS peers, which would have
blocked DNS resolution outright on clusters with non-standard service CIDRs.

In-cluster DNS answers on the kube-dns Service ClusterIP, which a static
manifest cannot predict: it is 10.96.0.10 on classic service ranges and allocated
from public space (e.g. 34.118.224.0/20) on newer GKE clusters. NetworkPolicy
matches that VIP rather than the backend pods, so selectors alone do not cover it.
To avoid DNS outages, every static DNS rule must provide:
1. The 10.96.0.10/32 literal for the classic service range.
2. An 0.0.0.0/0 peer carrying an except list for any ClusterIP outside private space.
3. The except list must block at least RFC 1918 private subnets (10.0.0.0/8,
   172.16.0.0/12, 192.168.0.0/16) to prevent internal lateral movement.

Note on peer sets and house shape (#747 B5):
Do not assert strict byte-identity across all files. Today, seven copies carry a
three-entry except list (RFC 1918) and include 169.254.169.254/32, while
deploy/kustomize/platform/networkpolicy-core-egress.yaml carries the 5-entry house
shape (adding 100.64.0.0/10 and 169.254.0.0/16, which is more contained and
mirrors the operator-generated external egress policy). This script enforces the
required-peer shape across all copies so drift is caught before merge.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

try:
    import yaml
except ImportError:
    sys.exit(
        "ERROR: scripts/check_iac_parity.py requires PyYAML.\n"
        "Install it with: pip install pyyaml (or make test-python-deps)"
    )

REPO_ROOT = Path(__file__).resolve().parent.parent

STATIC_NETWORK_POLICIES: tuple[str, ...] = (
    "charts/kube-agents/templates/litellm.yaml",
    "charts/kube-agents/templates/github-minter.yaml",
    "deploy/kustomize/platform/networkpolicy-core-egress.yaml",
    "examples/litellm-chatgpt-subscription/networkpolicy.yaml",
    "examples/litellm-gemini/networkpolicy.yaml",
    "examples/vllm-gemma/networkpolicy.yaml",
    "k8s-operator/config/integrations/github/deployment.yaml.template",
    "k8s-operator/config/integrations/litellm/base/networkpolicy.yaml",
)

# Manifests containing kind: NetworkPolicy that are deliberately excluded from the
# static DNS parity roster. Every entry must carry its reviewed reason.
# Note: Go test fixtures under testdata/ are ignored systematically via IGNORED_DIRS.
EXCLUDED_NETPOL_MANIFESTS: dict[str, str] = {}

IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".venv",
        "node_modules",
        "__pycache__",
        ".git",
        ".coverage-data",
        ".terraform",
        ".claude",
        "docs/site",
        "testdata",
    }
)

REQUIRED_DNS_LITERAL = "10.96.0.10/32"
REQUIRED_WILDCARD_CIDR = "0.0.0.0/0"
REQUIRED_EXCEPT_MINIMUM: frozenset[str] = frozenset(
    {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"}
)
HOUSE_SHAPE_EXCEPT: frozenset[str] = frozenset(
    {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16"}
)


def is_house_shape(except_list: Iterable[str]) -> bool:
    """Return True if the except list satisfies the 5-entry house shape."""
    return HOUSE_SHAPE_EXCEPT.issubset(set(except_list))


def sanitize_helm_template(text: str) -> str:
    """Sanitize Go/Helm template tags so that YAML parsers can load the manifests."""
    # Strip multi-line comments: {{- /* ... */ -}} or {{/* ... */}}
    text = re.sub(r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", "", text, flags=re.DOTALL)
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("{{") and stripped.endswith("}}"):
            # Whole line is a template control statement
            lines.append("# " + line)
        else:
            # Replace inline template expressions like {{ .Release.Namespace }}
            cleaned = re.sub(r"\{\{.*?\}\}", "placeholder", line)
            lines.append(cleaned)
    return "\n".join(lines)


def load_network_policies(path: Path) -> list[dict]:
    """Read a manifest or template file and return all NetworkPolicy documents.

    Parses document-by-document so syntax anomalies in unrelated manifests (like
    Deployments or ConfigMaps in the same template file) do not fail NetworkPolicy
    extraction.
    """
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    raw_content = path.read_text(encoding="utf-8")
    sanitized = sanitize_helm_template(raw_content)
    policies: list[dict] = []

    # Split documents on YAML boundary '---'
    for chunk in re.split(r"^---(?:\s.*)?$", sanitized, flags=re.MULTILINE):
        chunk_stripped = chunk.strip()
        if not chunk_stripped:
            continue
        # Only parse chunks that declare kind: NetworkPolicy
        if not re.search(r"^\s*kind:\s*['\"]?NetworkPolicy['\"]?", chunk_stripped, flags=re.MULTILINE):
            continue
        try:
            doc = yaml.safe_load(chunk_stripped)
            if isinstance(doc, dict) and doc.get("kind") == "NetworkPolicy":
                policies.append(doc)
        except Exception as exc:
            raise ValueError(f"malformed NetworkPolicy document in {path}: {exc}") from exc

    return policies


def discover_dns_network_policies(root: Path = REPO_ROOT) -> set[str]:
    """Scan the repository tree for all manifest files defining a port-53 DNS egress rule.

    Mirroring scripts/test_test_discovery.py, this discovery scan prevents any unlisted
    DNS-bearing NetworkPolicy from escaping the static parity guard.

    Returns:
        set[str]: set of repo-relative POSIX file paths.
    """
    discovered: set[str] = set()
    patterns = ("*.yaml", "*.yml", "*.yaml.template", "*.yml.template")
    for pattern in patterns:
        for path in root.rglob(pattern):
            if any(part in IGNORED_DIRS for part in path.parts):
                continue
            rel = path.relative_to(root).as_posix()
            if rel in EXCLUDED_NETPOL_MANIFESTS:
                continue
            try:
                raw = path.read_text(encoding="utf-8")
            except Exception:
                continue
            # Fast check before parsing
            if "NetworkPolicy" not in raw or "53" not in raw:
                continue
            try:
                policies = load_network_policies(path)
            except Exception:
                continue
            for pol in policies:
                spec = pol.get("spec") or {}
                for rule in spec.get("egress") or []:
                    if not isinstance(rule, dict):
                        continue
                    ports = rule.get("ports") or []
                    if any(isinstance(p, dict) and p.get("port") in (53, "53") for p in ports):
                        discovered.add(rel)
                        break
    return discovered


def check_dns_egress_rule(
    path_display: str,
    policy_name: str,
    rule: dict,
    rule_idx: int,
) -> list[str]:
    """Verify that a single port-53 egress rule satisfies the required peer shape."""
    errors: list[str] = []
    to_peers = rule.get("to") or []
    has_dns_literal = False
    has_wildcard = False
    wildcard_excepts: set[str] = set()

    for peer in to_peers:
        if not isinstance(peer, dict) or "ipBlock" not in peer:
            continue
        ip_block = peer["ipBlock"]
        if not isinstance(ip_block, dict):
            continue
        cidr = ip_block.get("cidr")
        if cidr == REQUIRED_DNS_LITERAL:
            has_dns_literal = True
        elif cidr == REQUIRED_WILDCARD_CIDR:
            has_wildcard = True
            except_list = ip_block.get("except")
            if isinstance(except_list, list):
                wildcard_excepts.update(str(x) for x in except_list)

    rule_desc = f"{path_display} (policy '{policy_name}', DNS rule #{rule_idx})"
    if not has_dns_literal:
        errors.append(
            f"{rule_desc}: missing required ipBlock literal '{REQUIRED_DNS_LITERAL}' for classic ClusterIP DNS"
        )
    if not has_wildcard:
        errors.append(
            f"{rule_desc}: missing required '{REQUIRED_WILDCARD_CIDR}' peer with except list for dynamic/public DNS VIPs"
        )
    else:
        missing_except = REQUIRED_EXCEPT_MINIMUM - wildcard_excepts
        if missing_except:
            errors.append(
                f"{rule_desc}: '{REQUIRED_WILDCARD_CIDR}' except list is missing required private CIDRs: "
                f"{sorted(missing_except)}"
            )

    return errors


def check_network_policy_file(path: Path, root: Path = REPO_ROOT) -> tuple[int, list[str]]:
    """Check all Egress NetworkPolicy resources in a file for DNS egress parity.

    Returns:
        tuple[int, list[str]]: (number of DNS rules checked, list of error messages)
    """
    rel_path = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    try:
        policies = load_network_policies(path)
    except Exception as exc:
        return 0, [f"{rel_path}: failed to load NetworkPolicy: {exc}"]

    if not policies:
        return 0, [f"{rel_path}: no NetworkPolicy resources found"]

    total_rules = 0
    errors: list[str] = []
    checked_policies = 0

    for policy in policies:
        policy_name = (policy.get("metadata") or {}).get("name") or "<unnamed>"
        spec = policy.get("spec") or {}
        policy_types = spec.get("policyTypes") or []
        egress_rules = spec.get("egress")

        # Skip Ingress-only policies that do not govern egress traffic.
        if "Egress" not in policy_types and egress_rules is None:
            continue

        checked_policies += 1
        dns_rules = [
            r
            for r in (egress_rules or [])
            if isinstance(r, dict)
            and any(
                isinstance(p, dict) and p.get("port") in (53, "53")
                for p in (r.get("ports") or [])
            )
        ]

        if not dns_rules:
            errors.append(
                f"{rel_path}: NetworkPolicy '{policy_name}' has no egress rule for port 53 (DNS)"
            )
            continue

        for idx, rule in enumerate(dns_rules, start=1):
            total_rules += 1
            rule_errors = check_dns_egress_rule(rel_path, policy_name, rule, idx)
            errors.extend(rule_errors)

    if checked_policies == 0:
        errors.append(f"{rel_path}: no Egress NetworkPolicy resources found")

    return total_rules, errors


def check_all(
    files: Iterable[Path | str] = STATIC_NETWORK_POLICIES,
    root: Path = REPO_ROOT,
    verbose: bool = False,
) -> tuple[int, list[str]]:
    """Check all specified files for DNS egress parity."""
    total_rules = 0
    all_errors: list[str] = []

    for item in files:
        path = root / item if not Path(item).is_absolute() else Path(item)
        rules_checked, file_errors = check_network_policy_file(path, root)
        total_rules += rules_checked
        all_errors.extend(file_errors)
        if verbose:
            rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
            if file_errors:
                print(f"  FAIL: {rel} ({len(file_errors)} errors)")
            else:
                print(f"  OK:   {rel} ({rules_checked} DNS rules verified)")

    return total_rules, all_errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify DNS egress rule parity across static NetworkPolicy copies."
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print verbose status for each verified file.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Optional specific file paths to check (defaults to STATIC_NETWORK_POLICIES).",
    )
    args = parser.parse_args(argv)

    files = args.files if args.files else STATIC_NETWORK_POLICIES
    if args.verbose:
        print(f"Checking {len(files)} static NetworkPolicy copies for DNS egress parity...")

    rules_checked, errors = check_all(files=files, root=REPO_ROOT, verbose=args.verbose)

    if errors:
        print("ERROR: Static NetworkPolicy DNS egress parity check failed:", file=sys.stderr)
        for err in errors:
            print(f"  * {err}", file=sys.stderr)
        print(
            f"\n{len(errors)} error(s) found across {len(files)} static policy copies.",
            file=sys.stderr,
        )
        return 1

    if not args.verbose:
        print(f"OK: Verified DNS egress rule parity across {len(files)} static copies ({rules_checked} rules checked).")
    else:
        print(f"\nAll {len(files)} static policy copies passed parity check ({rules_checked} rules).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
