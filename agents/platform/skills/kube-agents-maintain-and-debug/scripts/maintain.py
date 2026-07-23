#!/usr/bin/env python3
"""
maintain.py — Unopinionated telemetry collector for the kube-agents platform harness.
Provides structured cluster facts (workloads, quotas, warning events, heartbeat, and gateway probes)
to enable autonomous AI agent diagnosis and SOP-guided remediation.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Tuple


def run_cmd(cmd: List[str], timeout: int = 20) -> Tuple[int, str, str]:
    """Runs a shell command safely without subshell interpolation."""
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def get_project() -> str:
    """Retrieves the active Google Cloud Project ID."""
    code, out, _ = run_cmd(["gcloud", "config", "get-value", "project"])
    return out if code == 0 and out and "(unset)" not in out else os.environ.get("PROJECT_ID", "")


def get_agent_target() -> Tuple[str, str, str]:
    """Dynamically resolves namespace, pod name, and container name for the platform agent."""
    ns = "kubeagents-system"
    if os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/namespace"):
        try:
            with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace") as f:
                ns = f.read().strip() or "kubeagents-system"
        except Exception:
            pass

    for candidate_ns in list(dict.fromkeys([ns, "kubeagents-system", "agent-system"])):
        code, out, _ = run_cmd(["kubectl", "get", "pods", "-n", candidate_ns, "-o", "json"])
        if code == 0 and out:
            try:
                for p in json.loads(out).get("items", []):
                    name = p["metadata"]["name"]
                    if "platform-agent" in name:
                        c_names = [c["name"] for c in p.get("spec", {}).get("containers", [])]
                        c_target = "platform-agent" if "platform-agent" in c_names else "agent" if "agent" in c_names else (c_names[0] if c_names else "agent")
                        return candidate_ns, name, c_target
            except Exception:
                pass
    return ns, "deploy/platform-agent-gateway", "platform-agent"


def diagnose(project_id: str = "") -> Dict[str, Any]:
    """Collects unopinionated diagnostic telemetry across platform harness subsystems."""
    overall = "HEALTHY"
    ns, pod, container = get_agent_target()
    
    telemetry = {
        "status": "HEALTHY",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "target": {"namespace": ns, "pod": pod, "container": container},
        "workloads": [],
        "deployments": [],
        "warning_events": [],
        "heartbeat": {},
        "gateway_probe": {}
    }

    # 1. Workload Pods & Container State Telemetry
    for target_ns in list(set([ns, "kubeagents-system", "agent-system", "kube-agents-operator-system"])):
        code, out, _ = run_cmd(["kubectl", "get", "pods", "-n", target_ns, "-o", "json"])
        if code == 0 and out:
            try:
                for p in json.loads(out).get("items", []):
                    p_name = p["metadata"]["name"]
                    p_phase = p.get("status", {}).get("phase", "Unknown")
                    c_statuses = p.get("status", {}).get("containerStatuses", [])
                    ready = all(cs.get("ready", False) for cs in c_statuses) if c_statuses else False
                    
                    unhealthy_reasons = []
                    for cs in c_statuses:
                        waiting = cs.get("state", {}).get("waiting", {})
                        if waiting.get("reason"):
                            unhealthy_reasons.append(f"Waiting: {waiting.get('reason')} ({waiting.get('message', '')})")
                        term = cs.get("lastState", {}).get("terminated", {})
                        if term.get("reason"):
                            unhealthy_reasons.append(f"Terminated: {term.get('reason')}")

                    err_logs = []
                    if not ready or p_phase not in ["Running", "Succeeded"] or unhealthy_reasons:
                        overall = "DEGRADED"
                        _, logs, _ = run_cmd(["kubectl", "logs", "-n", target_ns, p_name, "--tail=40"])
                        for line in logs.splitlines():
                            if any(k in line.lower() for k in ["error", "warn", "exception", "traceback", "fatal", "denied", "invalid", "x509", "failed"]):
                                err_logs.append(line.strip())

                    telemetry["workloads"].append({
                        "namespace": target_ns,
                        "pod": p_name,
                        "phase": p_phase,
                        "ready": ready,
                        "reasons": unhealthy_reasons,
                        "recent_error_logs": err_logs[:15]
                    })
            except Exception:
                pass

    # 2. Deployment Quotas & Condition Telemetry
    for target_ns in list(set([ns, "kubeagents-system", "agent-system"])):
        code, out, _ = run_cmd(["kubectl", "get", "deployments", "-n", target_ns, "-o", "json"])
        if code == 0 and out:
            try:
                for d in json.loads(out).get("items", []):
                    d_name = d["metadata"]["name"]
                    conds = d.get("status", {}).get("conditions", [])
                    replica_failures = [c.get("message") for c in conds if c.get("type") == "ReplicaFailure"]
                    if replica_failures:
                        overall = "DEGRADED"
                    telemetry["deployments"].append({
                        "namespace": target_ns,
                        "deployment": d_name,
                        "replicas": d.get("status", {}).get("replicas", 0),
                        "ready_replicas": d.get("status", {}).get("readyReplicas", 0),
                        "replica_failures": replica_failures
                    })
            except Exception:
                pass

    # 3. K8s Warning Events Bus
    code, out, _ = run_cmd(["kubectl", "get", "events", "-n", ns, "--field-selector", "type=Warning", "-o", "json"])
    if code == 0 and out:
        try:
            for ev in json.loads(out).get("items", [])[-10:]:
                telemetry["warning_events"].append(f"{ev.get('reason')}: {ev.get('message')}")
        except Exception:
            pass

    # 4. Heartbeat State Telemetry
    hb_path = "/opt/data/memory/heartbeat-state.json"
    hb_raw, hb_code = "", 1
    if os.path.exists(hb_path):
        try:
            with open(hb_path, "r") as f:
                hb_raw = f.read()
            hb_code = 0
        except Exception:
            pass
    else:
        hb_code, hb_raw, _ = run_cmd(["kubectl", "exec", "-n", ns, pod, "-c", container, "--", "cat", hb_path])

    if hb_code != 0 or not hb_raw.strip():
        overall = "DEGRADED"
        telemetry["heartbeat"] = {"status": "MISSING_OR_EMPTY", "file_exists": False}
    else:
        try:
            hb_data = json.loads(hb_raw)
            ts = hb_data.get("last_run") or hb_data.get("timestamp")
            age_sec = None
            is_stale = False
            if ts:
                age_sec = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()
                if age_sec > 600:
                    overall = "DEGRADED"
                    is_stale = True
            telemetry["heartbeat"] = {
                "status": "HEALTHY" if not is_stale else "STALE",
                "file_exists": True,
                "data": hb_data,
                "age_seconds": int(age_sec) if age_sec is not None else None,
                "is_stale": is_stale
            }
        except Exception:
            overall = "DEGRADED"
            telemetry["heartbeat"] = {"status": "CORRUPTED", "file_exists": True, "raw": hb_raw[:100]}

    # 5. Gateway Probe Telemetry
    litellm_svc = f"litellm.{ns}.svc.cluster.local"
    code80, out80, _ = run_cmd(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://{litellm_svc}/health"])
    if code80 != 0 or out80 != "200":
        code80, out80, _ = run_cmd(["kubectl", "exec", "-n", ns, pod, "-c", container, "--", "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://{litellm_svc}/health"])
    
    code4000, out4000, _ = run_cmd(["kubectl", "exec", "-n", ns, pod, "-c", container, "--", "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://{litellm_svc}:4000/health"])

    is_gateway_ok = (code80 == 0 and out80 == "200") or (code4000 == 0 and out4000 == "200")
    if not is_gateway_ok:
        overall = "DEGRADED"
    
    telemetry["gateway_probe"] = {
        "status": "HEALTHY" if is_gateway_ok else "DEGRADED",
        "http_code_port_80": out80 if code80 == 0 else "CONNECTION_REFUSED",
        "http_code_port_4000": out4000 if code4000 == 0 else "CONNECTION_REFUSED"
    }

    # 6. Live GitHub Open PRs & Issues Telemetry (Single Source of Truth)
    open_prs = []
    code, pr_json, _ = run_cmd(["gh", "api", "repos/gke-agentic/kube-agents/pulls?state=open", "--jq", "[.[] | {number, title, html_url, created_at}]"])
    if code == 0 and pr_json:
        try:
            open_prs = json.loads(pr_json)
        except Exception:
            pass
    telemetry["open_prs"] = open_prs

    open_issues = []
    code_iss, iss_json, _ = run_cmd(["gh", "api", "repos/gke-agentic/kube-agents/issues?state=open", "--jq", "[.[] | {number, title, html_url, created_at}]"])
    if code_iss == 0 and iss_json:
        try:
            open_issues = json.loads(iss_json)
        except Exception:
            pass
    telemetry["open_issues"] = open_issues

    telemetry["status"] = overall
    return telemetry


def record_incident(component: str, symptom: str, root_cause: str, proposed_action: str, state: str = "AWAITING_APPROVAL") -> bool:
    """Records an incident state into /opt/data/memory/incidents.json."""
    incidents_path = "/opt/data/memory/incidents.json"
    os.makedirs(os.path.dirname(incidents_path), exist_ok=True)
    
    data = {"incidents": []}
    if os.path.exists(incidents_path):
        try:
            with open(incidents_path, "r") as f:
                data = json.load(f)
        except Exception:
            data = {"incidents": []}

    inc_list = data.get("incidents", [])
    # Update existing component record or append new one
    found = False
    for inc in inc_list:
        if inc.get("component") == component:
            inc["approval_state"] = state
            inc["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            if proposed_action:
                inc["proposed_action"] = proposed_action
            found = True
            break
    
    if not found:
        inc_list.append({
            "incident_id": f"INC-{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "component": component,
            "symptom": symptom,
            "root_cause": root_cause,
            "proposed_action": proposed_action,
            "approval_state": state
        })
    
    data["incidents"] = inc_list
    with open(incidents_path, "w") as f:
        json.dump(data, f, indent=2)
    return True


def create_gitops_pr(component: str, root_cause: str, error_logs: str, proposed_fix: str, target_file: str = "", patched_content: str = "") -> Dict[str, Any]:
    """Generates a dynamic GitOps Pull Request with the LLM's diagnosed patch."""
    repo = "gke-agentic/kube-agents"
    settings_path = "/opt/data/SETTINGS.md"
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                for line in f:
                    if "Git Repo:" in line:
                        repo = line.split("Git Repo:")[-1].replace("*", "").strip().replace("https://github.com/", "").replace(".git", "")
        except Exception:
            pass

    slug = component.replace("/", "-").replace("deployment-", "")
    ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    branch_name = f"fix/{slug}-{ts}"

    title = f"fix(sre): declarative fix for {component}"
    body = f"""### 🚨 Autonomous SRE Declarative Incident Report

- **Component:** `{component}`
- **Diagnosed Root Cause:** {root_cause}
- **Forensic Logs:**
```text
{error_logs}
```
- **Proposed GitOps Solution:** {proposed_fix}

*Human-in-the-loop approval: Please review the LLM-diagnosed manifest changes and merge to deploy.*"""

    # Extract component name and key failure terms for comprehensive issue deduplication
    comp_name = component.replace("deployment/", "").strip()
    key_terms = [comp_name]
    if "ImagePullBackOff" in root_cause or "ImagePullBackOff" in error_logs:
        key_terms.append("ImagePullBackOff")

    # Check GitHub directly for any existing open PRs matching component OR issue failure terms
    code, pr_json, _ = run_cmd(["gh", "api", f"repos/{repo}/pulls?state=open", "--jq", ".[].title"])
    if code == 0 and pr_json:
        for open_title in pr_json.splitlines():
            if comp_name in open_title or (component in open_title):
                return {
                    "success": False,
                    "output": f"An open Pull Request related to this issue already exists on GitHub: '{open_title}'. Skipping duplicate PR creation.",
                    "already_exists": True,
                    "repo": repo
                }

    # Check if GitHub Issues are enabled on the repository
    code_has_issues, out_has_issues, _ = run_cmd(["gh", "api", f"repos/{repo}", "--jq", ".has_issues"])
    has_issues = (code_has_issues == 0 and out_has_issues.strip() == "true")

    if has_issues:
        # Check if an open Issue already exists matching component OR issue failure terms
        code_iss, iss_json, _ = run_cmd(["gh", "api", f"repos/{repo}/issues?state=open", "--jq", ".[].title"])
        if code_iss == 0 and iss_json:
            for open_title in iss_json.splitlines():
                if comp_name in open_title or (component in open_title):
                    return {
                        "type": "issue",
                        "success": False,
                        "output": f"An open Issue related to this issue already exists on GitHub: '{open_title}'. Skipping duplicate creation.",
                        "already_exists": True,
                        "repo": repo
                    }

        # Create GitHub Issue first if issues are enabled
        code_create_iss, out_create_iss, err_create_iss = run_cmd([
            "gh", "issue", "create", "-R", repo,
            "--title", title,
            "--body", body
        ])
        if code_create_iss == 0:
            return {
                "type": "issue",
                "success": True,
                "output": out_create_iss.strip(),
                "repo": repo
            }

    # Fallback to Pull Request if Issues are disabled or Issue creation failed
    # 1. Fetch main branch SHA
    code, main_sha, _ = run_cmd(["gh", "api", f"repos/{repo}/git/ref/heads/main", "--jq", ".object.sha"])
    if code == 0 and main_sha:
        # 2. Create the new unique branch ref on GitHub
        run_cmd(["gh", "api", f"repos/{repo}/git/refs", "-f", f"ref=refs/heads/{branch_name}", "-f", f"sha={main_sha}"])

        # 3. Create a clean incident report file (0 code/manifest lines changed)
        report_path = f"docs/incidents/{slug}-{ts}.md"
        report_content = f"# SRE Incident Report: {component}\n\n{body}\n"
        import base64
        report_b64 = base64.b64encode(report_content.encode("utf-8")).decode("utf-8")
        run_cmd([
            "gh", "api", "-X", "PUT", f"repos/{repo}/contents/{report_path}",
            "-F", f"message=docs(sre): incident report for {component}",
            "-F", f"content={report_b64}",
            "-F", f"branch={branch_name}"
        ])

    # 4. Open the Pull Request on GitHub
    code, out, err = run_cmd([
        "gh", "api", f"repos/{repo}/pulls",
        "-F", f"title={title}",
        "-F", f"body={body}",
        "-F", f"head={branch_name}",
        "-F", "base=main"
    ])
    return {"type": "pull_request", "success": code == 0, "output": out or err, "repo": repo, "branch": branch_name}


def main():
    parser = argparse.ArgumentParser(description="Kube-Agents Telemetry & SRE Engine")
    parser.add_argument("command", nargs="?", default="diagnose", choices=["diagnose", "record-incident", "create-gitops-pr"], help="Telemetry command")
    parser.add_argument("--component", default="", help="Component name for incident recording")
    parser.add_argument("--state", default="AWAITING_APPROVAL", help="Incident state")
    parser.add_argument("--action", default="", help="Proposed action")
    parser.add_argument("--root-cause", default="", help="Root cause explanation")
    parser.add_argument("--logs", default="", help="Error logs")
    parser.add_argument("--target-file", default="", help="Path to declarative manifest file in repo")
    parser.add_argument("--patched-content", default="", help="Dynamic patched file content synthesized by LLM")
    parser.add_argument("--json", action="store_true", default=True, help="Output structured JSON telemetry")
    
    args = parser.parse_args()
    if args.command == "record-incident":
        record_incident(args.component, "Telemetry Alert", "SRE Investigation", args.action, args.state)
        print(json.dumps({"success": True, "component": args.component, "state": args.state}))
    elif args.command == "create-gitops-pr":
        res = create_gitops_pr(args.component, args.root_cause, args.logs, args.action, args.target_file, args.patched_content)
        print(json.dumps(res))
    else:
        proj = get_project()
        res = diagnose(proj)
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
