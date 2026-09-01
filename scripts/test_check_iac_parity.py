#!/usr/bin/env python3
"""Tests for scripts/check_iac_parity.py."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_iac_parity import (
    HOUSE_SHAPE_EXCEPT,
    REQUIRED_DNS_LITERAL,
    REQUIRED_EXCEPT_MINIMUM,
    REQUIRED_WILDCARD_CIDR,
    STATIC_NETWORK_POLICIES,
    check_all,
    check_dns_egress_rule,
    check_network_policy_file,
    is_house_shape,
    load_network_policies,
    main,
    sanitize_helm_template,
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
        self.assertEqual(
            rules_checked,
            len(STATIC_NETWORK_POLICIES),
            f"Expected {len(STATIC_NETWORK_POLICIES)} DNS rules checked, got {rules_checked}",
        )

    def test_roster_contains_all_known_copies(self):
        expected_subset = {
            "charts/kube-agents/templates/litellm.yaml",
            "charts/kube-agents/templates/github-minter.yaml",
            "deploy/kustomize/platform/networkpolicy-core-egress.yaml",
            "examples/litellm-chatgpt-subscription/networkpolicy.yaml",
            "examples/litellm-gemini/networkpolicy.yaml",
            "examples/vllm-gemma/networkpolicy.yaml",
            "k8s-operator/config/integrations/github/deployment.yaml.template",
            "k8s-operator/config/integrations/litellm/base/networkpolicy.yaml",
        }
        self.assertTrue(expected_subset.issubset(set(STATIC_NETWORK_POLICIES)))


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


if __name__ == "__main__":
    unittest.main()
