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

    telemetry["status"] = overall
    return telemetry


def main():
    parser = argparse.ArgumentParser(description="Kube-Agents Telemetry Collector")
    parser.add_argument("command", nargs="?", default="diagnose", choices=["diagnose"], help="Telemetry command")
    parser.add_argument("--json", action="store_true", default=True, help="Output structured JSON telemetry")
    
    args = parser.parse_args()
    proj = get_project()
    res = diagnose(proj)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
