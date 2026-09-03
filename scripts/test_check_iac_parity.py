#!/usr/bin/env python3
"""Tests for scripts/check_iac_parity.py."""

from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_iac_parity import (
    EXCLUDED_NETPOL_MANIFESTS,
    HOUSE_SHAPE_EXCEPT,
    REPO_ROOT,
    REQUIRED_DNS_LITERAL,
    REQUIRED_EXCEPT_MINIMUM,
    REQUIRED_WILDCARD_CIDR,
    STATIC_NETWORK_POLICIES,
    check_all,
    check_dns_egress_rule,
    check_network_policy_file,
    discover_dns_network_policies,
    is_house_shape,
    load_network_policies,
    main,
    sanitize_helm_template,
    validate_exclusions,
)


class CheckIacParityProductionTest(unittest.TestCase):
    """Verify that all live production static copies satisfy the DNS peer shape."""

    def test_all_production_copies_pass(self):
        rules_checked, errors = check_all()
        self.assertEqual(
            errors,
            [],
            f"Expected all production static policies to pass parity check, but found errors: {errors}",
        )
        self.assertGreaterEqual(
            rules_checked,
            len(STATIC_NETWORK_POLICIES),
            f"Expected at least {len(STATIC_NETWORK_POLICIES)} DNS rules checked, got {rules_checked}",
        )

    def test_discovery_matches_roster_exactly(self):
        """Ensure no DNS NetworkPolicy in the repository escapes the static roster.

        Mirroring scripts/test_test_discovery.py: any static manifest in the tree
        containing a port-53 egress rule must either be in STATIC_NETWORK_POLICIES
        or explicitly listed in EXCLUDED_NETPOL_MANIFESTS with a reviewed reason.
        """
        exclusion_errors = validate_exclusions(root=REPO_ROOT)
        self.assertEqual(
            exclusion_errors,
            [],
            f"EXCLUDED_NETPOL_MANIFESTS failed contract validation: {exclusion_errors}",
        )

        discovered = discover_dns_network_policies()
        roster = set(STATIC_NETWORK_POLICIES)

        untracked = discovered - roster
        self.assertEqual(
            untracked,
            set(),
            f"Found DNS-bearing NetworkPolicy files not tracked in STATIC_NETWORK_POLICIES: {sorted(untracked)}. "
            "Either add them to STATIC_NETWORK_POLICIES or to EXCLUDED_NETPOL_MANIFESTS with an explicit reason.",
        )

        stale = roster - discovered
        self.assertEqual(
            stale,
            set(),
            f"STATIC_NETWORK_POLICIES contains files that no longer contain DNS egress rules: {sorted(stale)}",
        )

    def test_excluded_manifests_contract(self):
        """Verify that EXCLUDED_NETPOL_MANIFESTS entries have non-empty reasons, point to existing files, and are disjoint from STATIC_NETWORK_POLICIES."""
        errors = validate_exclusions(root=REPO_ROOT)
        self.assertEqual(
            errors,
            [],
            f"EXCLUDED_NETPOL_MANIFESTS failed contract validation: {errors}",
        )


class CheckIacParitySyntheticTest(unittest.TestCase):
    """Verify that regressions in peer shape are caught as expected."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_manifest(self, filename: str, content: str) -> Path:
        p = self.root / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_discovery_catches_untracked_dns_policy(self):
        """Verify that discover_dns_network_policies flags newly introduced manifests."""
        manifest = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: new-service-egress
spec:
  policyTypes:
    - Egress
  egress:
    - ports:
        - port: 53
      to:
        - ipBlock:
            cidr: 10.96.0.10/32
"""
        self._write_manifest("examples/new-service/networkpolicy.yaml", manifest)
        discovered = discover_dns_network_policies(root=self.root)
        self.assertIn("examples/new-service/networkpolicy.yaml", discovered)

    def test_ignored_dirs_in_ancestor_path_does_not_break_discovery(self):
        """Verify that an ancestor path containing an ignored dirname (e.g. .claude/worktrees) does not suppress discovery."""
        worktree_root = self.root / ".claude" / "worktree"
        manifest = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: test-netpol
spec:
  egress:
    - ports:
        - port: 53
      to:
        - ipBlock:
            cidr: 10.96.0.10/32
"""
        manifest_path = worktree_root / "manifest.yaml"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(manifest, encoding="utf-8")

        # An ignored directory within the root should still be skipped
        ignored_path = worktree_root / ".venv" / "manifest.yaml"
        ignored_path.parent.mkdir(parents=True, exist_ok=True)
        ignored_path.write_text(manifest, encoding="utf-8")

        discovered = discover_dns_network_policies(root=worktree_root)
        self.assertIn("manifest.yaml", discovered)
        self.assertNotIn(".venv/manifest.yaml", discovered)

    def test_ignored_prefixes_docs_site(self):
        """Verify that manifests under docs/site are ignored by prefix."""
        manifest = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: doc-netpol
spec:
  egress:
    - ports:
        - port: 53
      to:
        - ipBlock:
            cidr: 10.96.0.10/32
"""
        self._write_manifest("docs/site/manifest.yaml", manifest)
        discovered = discover_dns_network_policies(root=self.root)
        self.assertNotIn("docs/site/manifest.yaml", discovered)

    def test_discovery_raises_on_malformed_network_policy(self):
        """Verify that malformed YAML containing NetworkPolicy and 53 raises rather than being silently swallowed."""
        bad_yaml = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
spec: [unclosed json syntax with 53
"""
        self._write_manifest("invalid.yaml", bad_yaml)
        with self.assertRaises(ValueError):
            discover_dns_network_policies(root=self.root)

    def test_validate_exclusions_contract(self):
        """Verify that validate_exclusions flags empty reasons, missing files, and roster collisions."""
        self._write_manifest("valid.yaml", "dummy")
        # 1. Valid exclusion passes
        errors = validate_exclusions(
            exclusions={"valid.yaml": "reviewed test reason"},
            roster=set(),
            root=self.root,
        )
        self.assertEqual(errors, [])

        # 2. Empty or whitespace reason fails
        errors = validate_exclusions(
            exclusions={"valid.yaml": "   "},
            roster=set(),
            root=self.root,
        )
        self.assertTrue(any("non-empty reviewed reason" in err for err in errors))

        # 3. Missing file fails
        errors = validate_exclusions(
            exclusions={"nonexistent.yaml": "valid reason"},
            roster=set(),
            root=self.root,
        )
        self.assertTrue(any("does not exist on disk" in err for err in errors))

        # 4. Roster collision fails
        errors = validate_exclusions(
            exclusions={"valid.yaml": "valid reason"},
            roster={"valid.yaml"},
            root=self.root,
        )
        self.assertTrue(any("cannot also be in STATIC_NETWORK_POLICIES" in err for err in errors))

    def test_discovery_respects_excluded_netpol_manifests(self):
        """Verify that discover_dns_network_policies ignores entries listed in EXCLUDED_NETPOL_MANIFESTS."""
        manifest = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: test-netpol
spec:
  egress:
    - ports:
        - port: 53
      to:
        - ipBlock:
            cidr: 10.96.0.10/32
"""
        self._write_manifest("excluded.yaml", manifest)
        with unittest.mock.patch.dict(
            "scripts.check_iac_parity.EXCLUDED_NETPOL_MANIFESTS",
            {"excluded.yaml": "reviewed test reason"},
        ):
            discovered = discover_dns_network_policies(root=self.root)
            self.assertNotIn("excluded.yaml", discovered)

    def test_discovery_raises_on_unreadable_file(self):
        """Verify that read errors during discovery raise RuntimeError."""
        self._write_manifest("unreadable.yaml", "dummy")
        with unittest.mock.patch.object(
            Path, "read_text", side_effect=OSError("simulated permission error")
        ):
            with self.assertRaises(RuntimeError):
                discover_dns_network_policies(root=self.root)

    def test_missing_dns_literal_fails(self):
        manifest = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: test-netpol
spec:
  egress:
    - ports:
        - port: 53
          protocol: UDP
      to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
"""
        p = self._write_manifest("netpol.yaml", manifest)
        rules, errors = check_network_policy_file(p, self.root)
        self.assertEqual(rules, 1)
        self.assertTrue(any(REQUIRED_DNS_LITERAL in err for err in errors))

    def test_missing_wildcard_cidr_fails(self):
        manifest = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: test-netpol
spec:
  egress:
    - ports:
        - port: 53
          protocol: UDP
      to:
        - ipBlock:
            cidr: 10.96.0.10/32
"""
        p = self._write_manifest("netpol.yaml", manifest)
        rules, errors = check_network_policy_file(p, self.root)
        self.assertEqual(rules, 1)
        self.assertTrue(any(REQUIRED_WILDCARD_CIDR in err for err in errors))

    def test_wildcard_missing_except_list_fails(self):
        manifest = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: test-netpol
spec:
  egress:
    - ports:
        - port: 53
          protocol: UDP
      to:
        - ipBlock:
            cidr: 10.96.0.10/32
        - ipBlock:
            cidr: 0.0.0.0/0
"""
        p = self._write_manifest("netpol.yaml", manifest)
        rules, errors = check_network_policy_file(p, self.root)
        self.assertEqual(rules, 1)
        self.assertTrue(any("missing required private CIDRs" in err for err in errors))

    def test_incomplete_except_list_fails(self):
        manifest = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: test-netpol
spec:
  egress:
    - ports:
        - port: 53
          protocol: UDP
      to:
        - ipBlock:
            cidr: 10.96.0.10/32
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 172.16.0.0/12
              - 192.168.0.0/16
"""
        p = self._write_manifest("netpol.yaml", manifest)
        rules, errors = check_network_policy_file(p, self.root)
        self.assertEqual(rules, 1)
        self.assertTrue(any("10.0.0.0/8" in err for err in errors))

    def test_house_shape_except_passes(self):
        manifest = f"""apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: test-netpol
spec:
  egress:
    - ports:
        - port: 53
          protocol: UDP
        - port: 53
          protocol: TCP
      to:
        - ipBlock:
            cidr: 10.96.0.10/32
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
{chr(10).join(f"              - {cidr}" for cidr in sorted(HOUSE_SHAPE_EXCEPT))}
"""
        p = self._write_manifest("netpol.yaml", manifest)
        rules, errors = check_network_policy_file(p, self.root)
        self.assertEqual(rules, 1)
        self.assertEqual(errors, [])
        self.assertTrue(is_house_shape(HOUSE_SHAPE_EXCEPT))

    def test_string_port_representation_supported(self):
        manifest = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: test-netpol
spec:
  egress:
    - ports:
        - port: "53"
          protocol: UDP
      to:
        - ipBlock:
            cidr: 10.96.0.10/32
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
"""
        p = self._write_manifest("netpol.yaml", manifest)
        rules, errors = check_network_policy_file(p, self.root)
        self.assertEqual(rules, 1)
        self.assertEqual(errors, [])

    def test_multi_doc_ingress_only_policy_skipped(self):
        manifest = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: ingress-only-db
spec:
  policyTypes:
    - Ingress
  ingress:
    - ports:
        - port: 5432
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: app-egress
spec:
  policyTypes:
    - Egress
  egress:
    - ports:
        - port: 53
      to:
        - ipBlock:
            cidr: 10.96.0.10/32
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
"""
        p = self._write_manifest("multi.yaml", manifest)
        rules, errors = check_network_policy_file(p, self.root)
        self.assertEqual(rules, 1)
        self.assertEqual(errors, [])

    def test_multi_doc_unrelated_syntax_error_ignored(self):
        manifest = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: broken-unrelated
spec: [unclosed json syntax
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: valid-netpol
spec:
  egress:
    - ports:
        - port: 53
      to:
        - ipBlock:
            cidr: 10.96.0.10/32
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
"""
        p = self._write_manifest("resilient.yaml", manifest)
        rules, errors = check_network_policy_file(p, self.root)
        self.assertEqual(rules, 1)
        self.assertEqual(errors, [])

    def test_missing_dns_rule_fails(self):
        manifest = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: test-netpol
spec:
  egress:
    - ports:
        - port: 443
          protocol: TCP
      to:
        - ipBlock:
            cidr: 0.0.0.0/0
"""
        p = self._write_manifest("netpol.yaml", manifest)
        rules, errors = check_network_policy_file(p, self.root)
        self.assertEqual(rules, 0)
        self.assertTrue(any("has no egress rule for port 53" in err for err in errors))

    def test_non_existent_file_fails(self):
        p = self.root / "does_not_exist.yaml"
        rules, errors = check_network_policy_file(p, self.root)
        self.assertEqual(rules, 0)
        self.assertTrue(any("failed to load NetworkPolicy" in err for err in errors))

    def test_helm_template_sanitization(self):
        raw = """{{- /* Multi-line comment
that should be
stripped */ -}}
{{- if .Values.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: test-netpol
  namespace: {{ .Release.Namespace }}
spec:
  egress:
    - ports:
        - port: 53
      to:
        - ipBlock:
            cidr: 10.96.0.10/32
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
{{- end }}"""
        p = self._write_manifest("template.yaml", raw)
        rules, errors = check_network_policy_file(p, self.root)
        self.assertEqual(rules, 1)
        self.assertEqual(errors, [])

    def test_split_wildcard_peers_fails(self):
        """Verify that splitting required private CIDRs across multiple 0.0.0.0/0 peers is rejected."""
        manifest = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: test-netpol
spec:
  egress:
    - ports:
        - port: 53
      to:
        - ipBlock:
            cidr: 10.96.0.10/32
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 172.16.0.0/12
              - 192.168.0.0/16
"""
        p = self._write_manifest("split_wildcard.yaml", manifest)
        rules, errors = check_network_policy_file(p, self.root)
        self.assertEqual(rules, 1)
        self.assertTrue(any("missing required private CIDRs" in err for err in errors))
        self.assertTrue(any("expected at most one '0.0.0.0/0' peer" in err for err in errors))

    def test_multiple_wildcard_peers_rejected(self):
        """Verify that multiple 0.0.0.0/0 peers in a single rule are rejected."""
        manifest = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: test-netpol
spec:
  egress:
    - ports:
        - port: 53
      to:
        - ipBlock:
            cidr: 10.96.0.10/32
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
"""
        p = self._write_manifest("dup_wildcard.yaml", manifest)
        rules, errors = check_network_policy_file(p, self.root)
        self.assertEqual(rules, 1)
        self.assertTrue(any("expected at most one '0.0.0.0/0' peer" in err for err in errors))

    def test_helm_template_trailing_comment(self):
        """Verify that template directives with trailing comments parse cleanly."""
        raw = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: test-netpol
spec:
  egress:
    {{- if .Values.enabled }} # conditionally included
    - ports:
        - port: 53
      to:
        - ipBlock:
            cidr: 10.96.0.10/32
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
    {{- end }} # end of condition
"""
        p = self._write_manifest("template_comment.yaml", raw)
        rules, errors = check_network_policy_file(p, self.root)
        self.assertEqual(rules, 1)
        self.assertEqual(errors, [])

    def test_helm_template_inline_conditional(self):
        """Verify that inline conditionals preserve the manifest payload."""
        raw = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: test-netpol
spec:
  egress:
    - ports:
        - port: 53
      to:
        {{- if .Values.includeClassic }}- ipBlock: { cidr: 10.96.0.10/32 }{{- end }}
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
"""
        p = self._write_manifest("template_inline.yaml", raw)
        rules, errors = check_network_policy_file(p, self.root)
        self.assertEqual(rules, 1)
        self.assertEqual(errors, [])

    def test_ingress_only_with_empty_egress_skipped(self):
        """Verify that Ingress-only policies with explicit egress: [] are skipped."""
        manifest = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: ingress-only
spec:
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector: {}
  egress: []
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: valid-egress
spec:
  policyTypes:
    - Egress
  egress:
    - ports:
        - port: 53
      to:
        - ipBlock:
            cidr: 10.96.0.10/32
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
"""
        p = self._write_manifest("ingress_empty_egress.yaml", manifest)
        rules, errors = check_network_policy_file(p, self.root)
        self.assertEqual(rules, 1)
        self.assertEqual(errors, [])

    def test_main_success_default(self):
        self.assertEqual(main([]), 0)

    def test_main_verbose(self):
        self.assertEqual(main(["-v"]), 0)

    def test_main_failure(self):
        manifest = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: test-netpol
spec:
  egress:
    - ports:
        - port: 53
      to:
        - ipBlock:
            cidr: 0.0.0.0/0
"""
        p = self._write_manifest("failing.yaml", manifest)
        self.assertEqual(main([str(p)]), 1)


if __name__ == "__main__":
    unittest.main()
