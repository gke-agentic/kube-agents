#!/usr/bin/env python3
"""
maintain.py — Unopinionated telemetry collector for the kube-agents platform harness.
Provides structured cluster facts (workloads, quotas, warning events, heartbeat, and gateway probes)
to enable autonomous AI agent diagnosis and SOP-guided remediation.
"""

import argparse
import base64
import datetime
import json
import os
import re
import subprocess
from typing import Any, Dict, List, Tuple

SECRET_PATTERNS = [
    # Bearer / Auth tokens
    (re.compile(r'(?i)(bearer\s+)[A-Za-z0-9\-\._~\+\/]+=*'), r'\1[REDACTED_TOKEN]'),
    # Specific Cloud API Keys (GCP, AWS)
    (re.compile(r'AIzaSy[A-Za-z0-9\-_]{33}'), r'[REDACTED_GCP_API_KEY]'),
    (re.compile(r'AKIA[0-9A-Z]{16}'), r'[REDACTED_AWS_KEY]'),
    # Self-identifying tokens (GitHub, GCP OAuth, Slack, JWTs)
    (re.compile(r'gh[pousr]_[A-Za-z0-9]{16,}'), r'[REDACTED_GITHUB_TOKEN]'),
    (re.compile(r'github_pat_[A-Za-z0-9_]{20,}'), r'[REDACTED_GITHUB_TOKEN]'),
    (re.compile(r'ya29\.[A-Za-z0-9._\-]{20,}'), r'[REDACTED_GCP_TOKEN]'),
    (re.compile(r'xox[baprs]-[A-Za-z0-9\-]{10,}'), r'[REDACTED_SLACK_TOKEN]'),
    (re.compile(r'eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}'), r'[REDACTED_JWT]'),
    # Private keys
    (re.compile(r'-----BEGIN[A-Z\s]+PRIVATE KEY-----[\s\S]*?-----END[A-Z\s]+PRIVATE KEY-----'), r'[REDACTED_PRIVATE_KEY]'),
    (re.compile(r'"private_key"\s*:\s*"[^"]+"'), r'"private_key": "[REDACTED_PRIVATE_KEY]"'),
    # Generic API Keys / Tokens
    (re.compile(r'(?i)(api[_-]?key|access[_-]?token|auth[_-]?token|secret[_-]?key)\s*[:=]\s*["\']?[A-Za-z0-9\-\._]{16,}["\']?'), r'\1=[REDACTED_SECRET]'),
    # Password / credentials in strings or URLs
    (re.compile(r'(?i)(password|passwd|pwd)\s*[:=]\s*["\']?\S+["\']?'), r'\1=[REDACTED_PASSWORD]'),
    (re.compile(r'https?://[^:\s"\']+:[^@\s"\']+@'), r'https://[REDACTED_CREDS]@'),
]


def redact_secrets(text: str) -> str:
    """Deterministic redaction of sensitive credentials, keys, and tokens from text strings."""
    if not text:
        return ""
    scrubbed = text
    for pattern, replacement in SECRET_PATTERNS:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed


def run_cmd(cmd: List[str], timeout: int = 20) -> Tuple[int, str, str]:
    """Runs a shell command safely without subshell interpolation."""
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def get_project() -> str:
    """Retrieves the active Google Cloud Project ID from environment variables."""
    return os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT") or ""


def parse_repo_url(url: str) -> str:
    """Parses git remote or settings URL into standard owner/repo format."""
    s = re.sub(r'^(https?|ssh|git)://(git@)?github\.com/', '', url.strip())
    s = re.sub(r'^git@github\.com:', '', s)
    s = re.sub(r'\.git/*$', '', s).strip('/')
    return s


def get_repo() -> str:
    """Dynamically resolves the target GitOps repository from SETTINGS.md, git remote origin, or environment."""
    settings_path = "/opt/data/SETTINGS.md"
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                for line in f:
                    if "Git Repo:" in line:
                        raw = line.split("Git Repo:")[-1].replace("*", "").strip()
                        if raw.lower() in ("none", ""):
                            continue
                        repo = parse_repo_url(raw)
                        if repo:
                            return repo
        except Exception:
            pass

    code, out, _ = run_cmd(["git", "config", "--get", "remote.origin.url"])
    if code == 0 and out.strip():
        url = parse_repo_url(out.strip())
        if url:
            return url

    env_repo = os.environ.get("GITHUB_REPOSITORY") or os.environ.get("GITOPS_REPO")
    if env_repo:
        url = parse_repo_url(env_repo.strip())
        if url:
            return url

    return ""


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


def get_target_namespaces(default_ns: str) -> List[str]:
    """Returns the subset of known candidate Kube-Agents namespaces that actually exist on this cluster."""
    known_candidates = ["kubeagents-system", default_ns, "agent-system", "kube-agents-operator-system"]
    code, out, _ = run_cmd(["kubectl", "get", "namespaces", "-o", "jsonpath={.items[*].metadata.name}"])
    if code == 0 and out.strip():
        cluster_ns = set(out.strip().split())
        existing = [n for n in list(dict.fromkeys(known_candidates)) if n in cluster_ns]
        if existing:
            return existing
    return [default_ns]


def get_pod_error_logs(namespace: str, pod_name: str, max_lines: int = 15) -> List[str]:
    """Retrieves container logs or K8s Warning Events for an unhealthy pod with deterministic secret redaction."""
    err_logs = []
    # 1. Try kubectl logs directly (works across all K8s clusters)
    for flag in [["--tail", str(max_lines)], ["--tail", str(max_lines), "--previous"]]:
        code_log, out_log, _ = run_cmd(["kubectl", "logs", "-n", namespace, pod_name] + flag, timeout=10)
        if code_log == 0 and out_log.strip():
            for line in out_log.strip().splitlines()[-max_lines:]:
                if line.strip():
                    scrubbed = redact_secrets(line.strip())
                    err_logs.append(f"[kubectl] {scrubbed}")
            if err_logs:
                return err_logs[:max_lines]

    # 2. Fall back to structured Kubernetes Warning Events for this specific pod
    code_ev, out_ev, _ = run_cmd(["kubectl", "get", "events", "-n", namespace, f"--field-selector=involvedObject.name={pod_name},type=Warning", "-o", "json"])
    if code_ev == 0 and out_ev:
        try:
            for ev in json.loads(out_ev).get("items", [])[-max_lines:]:
                reason = ev.get("reason", "Warning")
                message = redact_secrets(ev.get("message", ""))
                err_logs.append(f"[K8sEvent: {reason}] {message}")
        except Exception:
            pass

    return err_logs[:max_lines]


def diagnose() -> Dict[str, Any]:
    """Collects unopinionated diagnostic telemetry across platform harness subsystems."""
    overall = "HEALTHY"
    ns, pod, container = get_agent_target()
    target_namespaces = get_target_namespaces(ns)

    telemetry: Dict[str, Any] = {
        "status": "HEALTHY",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "target": {"namespace": ns, "pod": pod, "container": container},
        "workloads": [],
        "deployments": [],
        "warning_events": [],
        "heartbeat": {},
        "gateway_probe": {},
        "errors": []
    }

    # 1. Workload Pods & Container State Telemetry
    for target_ns in target_namespaces:
        code, out, err = run_cmd(["kubectl", "get", "pods", "-n", target_ns, "-o", "json"])
        if code == 0 and out:
            try:
                for p in json.loads(out).get("items", []):
                    p_name = p["metadata"]["name"]
                    p_phase = p.get("status", {}).get("phase", "Unknown")
                    c_statuses = p.get("status", {}).get("containerStatuses", [])
                    ready = all(cs.get("ready", False) for cs in c_statuses) if c_statuses else False

                    unhealthy_reasons = []
                    for cs in c_statuses:
                        for state_key in ["state"]:
                            st = cs.get(state_key, {})
                            term = st.get("terminated", {})
                            if term.get("reason") and term.get("reason") != "Completed":
                                msg = f"{state_key.capitalize()} Terminated ({term.get('reason')}, exitCode={term.get('exitCode')})"
                                if term.get("message"):
                                    msg += f": {term.get('message')}"
                                if msg not in unhealthy_reasons:
                                    unhealthy_reasons.append(msg)
                            waiting = st.get("waiting", {})
                            if waiting.get("reason"):
                                msg = f"Waiting: {waiting.get('reason')} ({waiting.get('message', '')})"
                                if msg not in unhealthy_reasons:
                                    unhealthy_reasons.append(msg)

                    restart_count = max([cs.get("restartCount", 0) for cs in c_statuses], default=0)
                    err_logs = []
                    if (p_phase == "Running" and not ready) or p_phase not in ["Running", "Succeeded"] or unhealthy_reasons:
                        overall = "DEGRADED"
                        err_logs = get_pod_error_logs(target_ns, p_name)

                    telemetry["workloads"].append({
                        "namespace": target_ns,
                        "pod": p_name,
                        "phase": p_phase,
                        "ready": ready,
                        "restart_count": restart_count,
                        "reasons": unhealthy_reasons,
                        "recent_error_logs": err_logs[:15]
                    })
            except Exception as e:
                overall = "DEGRADED"
                telemetry["errors"].append(f"Parsing pods json in {target_ns} failed: {str(e)}")
        elif code != 0 and err:
            overall = "DEGRADED"
            telemetry["errors"].append(f"kubectl get pods -n {target_ns} failed (code {code}): {err}")

    # 2. Deployment Quotas & Condition Telemetry
    for target_ns in target_namespaces:
        code, out, err = run_cmd(["kubectl", "get", "deployments", "-n", target_ns, "-o", "json"])
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
            except Exception as e:
                overall = "DEGRADED"
                telemetry["errors"].append(f"Parsing deployments json in {target_ns} failed: {str(e)}")
        elif code != 0 and err:
            overall = "DEGRADED"
            telemetry["errors"].append(f"kubectl get deployments -n {target_ns} failed (code {code}): {err}")

    # 3. K8s Warning Events Bus
    critical_warning_reasons = {
        "FailedScheduling",
        "Evicted",
        "FailedMount",
        "FailedAttachVolume",
        "CreateContainerConfigError",
        "CreateContainerError",
        "CannotEvictPod",
        "OOMKilled",
        "ErrImagePull",
        "ImagePullBackOff",
    }
    for target_ns in target_namespaces:
        code, out, err = run_cmd(["kubectl", "get", "events", "-n", target_ns, "--field-selector", "type=Warning", "--sort-by=.lastTimestamp", "-o", "json"])
        if code == 0 and out:
            try:
                for ev in json.loads(out).get("items", [])[-10:]:
                    obj = ev.get("involvedObject", {})
                    obj_name = obj.get("name", "")
                    obj_kind = obj.get("kind", "")
                    reason = ev.get("reason", "")
                    msg = redact_secrets(ev.get("message", ""))
                    if reason in critical_warning_reasons:
                        overall = "DEGRADED"
                    telemetry["warning_events"].append(f"[{target_ns}] {obj_kind}/{obj_name} ({reason}): {msg}")
            except Exception as e:
                telemetry["errors"].append(f"Parsing warning events json in {target_ns} failed: {str(e)}")

    # 4. Heartbeat State Telemetry
    hb_path = "/opt/data/memory/heartbeat-state.json"
    hb_raw, hb_code = "", 1
    if os.path.exists(hb_path):
        try:
            with open(hb_path, "r") as f:
                hb_raw = f.read()
            hb_code = 0
        except Exception as e:
            telemetry["errors"].append(f"Reading local heartbeat file failed: {str(e)}")
    else:
        hb_code, hb_raw, hb_err = run_cmd(["kubectl", "exec", "-n", ns, pod, "-c", container, "--", "cat", hb_path])
        if hb_code != 0 and hb_err:
            telemetry["errors"].append(f"kubectl exec cat heartbeat-state.json failed (code {hb_code}): {hb_err}")

    if hb_code != 0 or not hb_raw.strip():
        telemetry["heartbeat"] = {"status": "MISSING_OR_EMPTY", "file_exists": False}
    else:
        try:
            hb_data = json.loads(hb_raw)
            telemetry["heartbeat"] = {
                "status": "VALID",
                "file_exists": True,
                "data": hb_data
            }
        except Exception as e:
            telemetry["errors"].append(f"Parsing heartbeat json failed: {str(e)}")
            telemetry["heartbeat"] = {"status": "CORRUPTED", "file_exists": True, "raw": redact_secrets(hb_raw[:100])}

    # 5. Gateway Probe Telemetry
    litellm_ns = "kubeagents-system"
    for candidate_ns in target_namespaces:
        code_svc, _, _ = run_cmd(["kubectl", "get", "svc", "litellm", "-n", candidate_ns])
        if code_svc == 0:
            litellm_ns = candidate_ns
            break

    litellm_svc = f"litellm.{litellm_ns}.svc.cluster.local"
    code80, out80 = 1, "000"
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        code80, out80, _ = run_cmd(["curl", "-s", "--max-time", "5", "-o", "/dev/null", "-w", "%{http_code}", f"http://{litellm_svc}/health"])
    if code80 != 0 or out80 != "200":
        code80, out80, _ = run_cmd(["kubectl", "exec", "-n", ns, pod, "-c", container, "--", "curl", "-s", "--max-time", "5", "-o", "/dev/null", "-w", "%{http_code}", f"http://{litellm_svc}/health"])

    is_gateway_ok = (code80 == 0 and out80 == "200")
    if not is_gateway_ok:
        overall = "DEGRADED"

    telemetry["gateway_probe"] = {
        "status": "HEALTHY" if is_gateway_ok else "DEGRADED",
        "namespace": litellm_ns,
        "http_code_port_80": out80 if (code80 == 0 and out80 != "000") else f"PROBE_FAILED_CODE_{code80}"
    }

    # 6. Cluster Node Health & Pressure Telemetry
    telemetry["node_conditions"] = []
    code_node, out_node, err_node = run_cmd(["kubectl", "get", "nodes", "-o", "json"])
    if code_node == 0 and out_node:
        try:
            for node in json.loads(out_node).get("items", []):
                n_name = node["metadata"]["name"]
                conds = node.get("status", {}).get("conditions", [])
                for c in conds:
                    c_type = c.get("type")
                    c_status = c.get("status")
                    if c_type == "Ready" and c_status != "True":
                        overall = "DEGRADED"
                        telemetry["node_conditions"].append(f"Node {n_name} is NotReady")
                    elif c_type in ["MemoryPressure", "DiskPressure", "PIDPressure"] and c_status == "True":
                        overall = "DEGRADED"
                        telemetry["node_conditions"].append(f"Node {n_name} has {c_type}")
        except Exception as e:
            telemetry["errors"].append(f"Parsing node json failed: {str(e)}")
    elif code_node != 0 and err_node:
        telemetry["errors"].append(f"kubectl get nodes failed (code {code_node}): {err_node}")

    # 7. Live GitHub Open PRs & Issues Telemetry (Filtered by 'sre-incident-report' string in description, latest 100)
    repo = get_repo()
    open_prs = []
    open_issues = []
    if not repo:
        telemetry["errors"].append("Target GitOps repository is not configured in /opt/data/SETTINGS.md, git remote, or GITHUB_REPOSITORY.")
    else:
        code, pr_json, err_pr = run_cmd(["gh", "api", f"repos/{repo}/pulls?state=open&per_page=100", "--jq", "[.[] | select((.body // \"\") | contains(\"sre-incident-report\")) | {number, title, body}]"])
        if code == 0 and pr_json:
            try:
                open_prs = json.loads(pr_json)
            except Exception as e:
                telemetry["errors"].append(f"Parsing GitHub open PRs json failed: {str(e)}")
        elif code != 0 and err_pr:
            telemetry["errors"].append(f"gh api pulls list failed (code {code}): {err_pr}")

        code_iss, iss_json, err_iss = run_cmd(["gh", "api", f"repos/{repo}/issues?state=open&per_page=100", "--jq", "[.[] | select((.pull_request == null) and ((.body // \"\") | contains(\"sre-incident-report\"))) | {number, title, body}]"])
        if code_iss == 0 and iss_json:
            try:
                open_issues = json.loads(iss_json)
            except Exception as e:
                telemetry["errors"].append(f"Parsing GitHub open issues json failed: {str(e)}")
        elif code_iss != 0 and err_iss:
            telemetry["errors"].append(f"gh api issues list failed (code {code_iss}): {err_iss}")

    telemetry["open_prs"] = open_prs
    telemetry["open_issues"] = open_issues

    telemetry["status"] = overall
    return telemetry


def sanitize_component_path(component: str) -> str:
    """Sanitizes an LLM-provided component string into a safe slug [a-z0-9-]."""
    clean_segments = []
    for seg in component.split("/"):
        s = re.sub(r'[^a-z0-9]+', '-', seg.lower()).strip('-')
        s = re.sub(r'-+', '-', s)
        if s:
            clean_segments.append(s[:30])
    path = "-".join(clean_segments[:4])
    return path or "workload"


def create_gitops_pr(component: str, root_cause: str, error_logs: str, proposed_fix: str) -> Dict[str, Any]:
    """Generates a dynamic GitOps Pull Request or GitHub Issue with code-level deduplication and secret redaction."""
    repo = get_repo()
    if not repo:
        return {
            "type": "error",
            "success": False,
            "output": "No GitOps repository configured in /opt/data/SETTINGS.md, git remote, or GITHUB_REPOSITORY.",
            "repo": ""
        }

    slug_path = sanitize_component_path(component)

    # Redact secrets deterministically from all LLM-supplied input fields before formatting
    clean_component = redact_secrets(component)
    clean_root_cause = redact_secrets(root_cause)
    clean_error_logs = redact_secrets(error_logs)
    clean_proposed_fix = redact_secrets(proposed_fix)

    # Code-Enforced Deduplication: Check if an open PR or Issue already exists for this component slug
    code_prs, pr_json, _ = run_cmd(["gh", "api", f"repos/{repo}/pulls?state=open&per_page=100", "--jq", "[.[] | select((.body // \"\") | contains(\"sre-incident-report\")) | {number, title, body, html_url}]"], timeout=15)
    if code_prs == 0 and pr_json:
        try:
            for pr in json.loads(pr_json):
                b = pr.get("body", "")
                t = pr.get("title", "")
                if slug_path in b or clean_component in t or f"`{clean_component}`" in b:
                    return {
                        "type": "duplicate",
                        "success": True,
                        "already_exists": True,
                        "output": f"Deduplication short-circuit: An open PR #{pr.get('number')} already exists for '{clean_component}' ({pr.get('html_url')})",
                        "repo": repo
                    }
        except Exception:
            pass

    code_iss, iss_json, _ = run_cmd(["gh", "api", f"repos/{repo}/issues?state=open&per_page=100", "--jq", "[.[] | select((.pull_request == null) and ((.body // \"\") | contains(\"sre-incident-report\"))) | {number, title, body, html_url}]"], timeout=15)
    if code_iss == 0 and iss_json:
        try:
            for issue in json.loads(iss_json):
                b = issue.get("body", "")
                t = issue.get("title", "")
                if slug_path in b or clean_component in t or f"`{clean_component}`" in b:
                    return {
                        "type": "duplicate",
                        "success": True,
                        "already_exists": True,
                        "output": f"Deduplication short-circuit: An open Issue #{issue.get('number')} already exists for '{clean_component}' ({issue.get('html_url')})",
                        "repo": repo
                    }
        except Exception:
            pass

    ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    branch_name = f"fix/{slug_path}-{ts}"

    title = f"docs(sre): incident report for {clean_component}"
    title_iss = f"[SRE Incident] {clean_component}: automated diagnosis"

    body = f"""### 🚨 Autonomous SRE Declarative Incident Report

- **Type:** `sre-incident-report`
- **Component:** `{clean_component}`
- **Diagnosed Root Cause:** {clean_root_cause}
- **Forensic Logs:**
```text
{clean_error_logs}
```
- **Proposed GitOps Solution:** {clean_proposed_fix}

*Human-in-the-loop approval: Please review the SRE diagnostic report and suggested remediation.*"""

    # Check if GitHub Issues are enabled on the repository
    code_has_issues, out_has_issues, _ = run_cmd(["gh", "api", f"repos/{repo}", "--jq", ".has_issues"], timeout=15)
    has_issues = (code_has_issues == 0 and out_has_issues.strip() == "true")

    if has_issues:
        # Create GitHub Issue if issues are enabled
        code_create_iss, out_create_iss, err_create_iss = run_cmd([
            "gh", "issue", "create", "-R", repo,
            "--title", title_iss,
            "--body", body
        ], timeout=15)
        if code_create_iss == 0:
            return {
                "type": "issue",
                "success": True,
                "output": out_create_iss.strip(),
                "repo": repo
            }
        else:
            return {
                "type": "issue",
                "success": False,
                "output": f"Failed to create GitHub Issue for repository '{repo}': {err_create_iss.strip()}",
                "repo": repo
            }

    # Fallback to Pull Request if Issues are disabled on the repository
    code_def, default_branch, err_def = run_cmd(["gh", "api", f"repos/{repo}", "--jq", ".default_branch"], timeout=15)
    if code_def != 0 or not default_branch.strip():
        return {"type": "pull_request", "success": False, "output": f"Failed to resolve default branch for repository '{repo}': {err_def}", "repo": repo}
    base_branch = default_branch.strip()

    # 1. Fetch base branch SHA
    code_sha, base_sha, err_sha = run_cmd(["gh", "api", f"repos/{repo}/git/ref/heads/{base_branch}", "--jq", ".object.sha"], timeout=15)
    if code_sha != 0 or not base_sha.strip():
        return {"type": "pull_request", "success": False, "output": f"Failed to resolve SHA for base branch '{base_branch}': {err_sha}", "repo": repo}
    base_sha_str = base_sha.strip()

    # 2. Create the new unique branch ref on GitHub
    code_ref, _, err_ref = run_cmd(["gh", "api", f"repos/{repo}/git/refs", "-f", f"ref=refs/heads/{branch_name}", "-f", f"sha={base_sha_str}"], timeout=15)
    if code_ref != 0:
        return {"type": "pull_request", "success": False, "output": f"Failed to create branch ref '{branch_name}': {err_ref}", "repo": repo, "branch": branch_name}

    # 3. Create a clean incident report file (0 code/manifest lines changed)
    report_path = f".incidents/{slug_path}-{ts}.md"
    report_content = f"# SRE Incident Report: {clean_component}\n\n{body}\n"
    report_b64 = base64.b64encode(report_content.encode("utf-8")).decode("utf-8")
    code_put, _, err_put = run_cmd([
        "gh", "api", "-X", "PUT", f"repos/{repo}/contents/{report_path}",
        "-f", f"message=docs(sre): incident report for {clean_component}",
        "-f", f"content={report_b64}",
        "-f", f"branch={branch_name}"
    ], timeout=15)
    if code_put != 0:
        return {"type": "pull_request", "success": False, "output": f"Failed to commit incident report file '{report_path}': {err_put}", "repo": repo, "branch": branch_name}

    # 4. Open the Pull Request on GitHub
    code_pr, out_pr, err_pr = run_cmd([
        "gh", "api", f"repos/{repo}/pulls",
        "-f", f"title={title}",
        "-f", f"body={body}",
        "-f", f"head={branch_name}",
        "-f", f"base={base_branch}"
    ], timeout=15)
    output_val = (out_pr or err_pr).strip()
    if code_pr == 0 and out_pr:
        try:
            pr_data = json.loads(out_pr)
            output_val = json.dumps({"number": pr_data.get("number"), "url": pr_data.get("html_url"), "title": pr_data.get("title")})
        except Exception:
            pass
    return {"type": "pull_request", "success": code_pr == 0, "output": output_val, "repo": repo, "branch": branch_name, "base": base_branch}


def main():
    parser = argparse.ArgumentParser(description="Kube-Agents Telemetry & SRE Engine")
    parser.add_argument("command", nargs="?", default="diagnose", choices=["diagnose", "create-gitops-pr"], help="Telemetry command")
    parser.add_argument("--component", default="", help="Component name")
    parser.add_argument("--action", default="", help="Proposed action")
    parser.add_argument("--root-cause", default="", help="Root cause explanation")
    parser.add_argument("--logs", default="", help="Error logs")
    parser.add_argument("--json", action="store_true", default=False, help="Output structured JSON telemetry (default format)")

    args = parser.parse_args()
    if args.command == "create-gitops-pr":
        res = create_gitops_pr(args.component, args.root_cause, args.logs, args.action)
        print(json.dumps(res))
    else:
        res = diagnose()
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
