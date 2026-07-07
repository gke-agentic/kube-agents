# Research Notes: Platform Agent BYO Regular Deployment Spike

This document captures the technical findings, root cause analyses (RCAs), architectural patterns, and validation results for migrating the Hermes Platform Agent to a regular Kubernetes deployment managed by `kagent` (v0.9.11) via the `type: BYO` (Bring-Your-Own) custom resource definition.

---

## 1. Final Status (Validated & Operational)

The `platform-agent` BYO deployment on GKE Standard (`kanget-with-root-agent` cluster, `kube-agents-regular-spike` namespace) is **fully operational, highly available, and verified**.
* **Pod Status:** `1/1 Running`, `Ready: True` (0 restarts).
* **Protocol Readiness:** `GET /.well-known/agent-card.json` returns HTTP 200 OK to Kubernetes readiness probes and `kagent-controller`.
* **UI & Dashboard Integration:** End-to-end chat execution verified from KAgent Harness UI (`http://localhost:8084`), bridging asynchronously into Hermes's internal `/v1/chat/completions` engine and recording live sessions in the Hermes Web UI Dashboard (`http://localhost:9119`).

---

## 2. Technical Findings & Root Cause Analysis

During the migration from legacy operator infrastructure to declarative `kagent` BYO deployments, we identified and resolved four critical technical blockers:

### Blocker A: BYO Deployment Selector Label Mismatch
* **Problem:** When deploying the `Agent` CRD (`type: BYO`), `kagent-controller` automatically generates a Kubernetes Deployment and injects standard matchLabels (`kagent: platform-agent`, `app.kubernetes.io/managed-by: kagent`, etc.). In our initial `agent.yaml.template`, we manually specified `labels: app: platform-agent` under the pod metadata template. This caused Kubernetes Deployment validation to fail because the manual pod labels did not satisfy the controller's generated selector requirements.
* **Remediation:** Removed hardcoded manual app labels from the `byo.deployment` spec in `agent.yaml.template`, allowing `kagent-controller` to exclusively manage and populate matching selector labels across the Deployment, ReplicaSet, and Pod templates.

### Blocker B: PersistentVolumeClaim (PVC) Filesystem Permission Denied
* **Problem:** The container image runs as non-root user `sandbox` (UID/GID 10001). When mounting the `platform-agent-data` PersistentVolumeClaim to `/opt/data`, GKE Standard formats the root volume directory with root-owned permissions (`0755 root:root`). Upon container boot, `docker-entrypoint.sh` attempted to copy default skills and scripts into `/opt/data/` (`cp -ru /opt/defaults/. /opt/data/`) and crashed with `Permission denied`.
* **Remediation:** Configured `podSecurityContext` directly on the BYO deployment template in `agent.yaml.template`:
  ```yaml
  podSecurityContext:
    fsGroup: 10001
    runAsUser: 10001
    runAsGroup: 10001
  ```
  This instructs the kubelet to recursively change group ownership of the mounted PVC filesystem to GID 10001 upon volume attachment, granting write access without requiring init containers or root privileges.

### Blocker C: KAgent UI Chat Hanging & Unknown Event Kind Errors (A2A v0.3 Protocol)
* **Problem:** When sending chat messages via KAgent UI (`http://localhost:8084`), the UI hung indefinitely in the "thinking" stage, or threw JSON-RPC errors: `unknown event kind: ` with `INTERNAL_ERROR`.
* **Analysis:**
  1. **Startup Hook Location:** Python virtual environments (`/opt/hermes/.venv/bin/python3`) disable user site packages (`site.ENABLE_USER_SITE = False`), causing scripts named `usercustomize.py` on `PYTHONPATH` to be silently ignored on boot. However, Python's core `site.py` *always* imports `sitecustomize` on boot.
  2. **aiohttp Router Freezing:** In `aiohttp`, once a web application starts listening, its routing table is frozen (`app.freeze()`). Calling `router.add_post(...)` after boot fails.
  3. **A2A v0.3 SSE Streaming Spec:** When KAgent UI communicates with a BYO agent, it sends an HTTP POST request to `/` invoking JSON-RPC method `"message/stream"` with header `Accept: text/event-stream`. In Server-Sent Events (SSE) streaming under A2A v0.3, each chunk MUST be prefixed with explicit event headers (`event: message\n` and `event: status\n`). Furthermore, the top-level JSON-RPC `result` MUST directly contain the message object (`{"kind": "message", ...}`) rather than nesting it inside an extra `"message"` property (`{"result": {"message": ...}}`). Nesting caused `kagent-controller` to evaluate `result["kind"]` as `None`.
* **Remediation:**
  1. Replaced `usercustomize.py` with `/opt/data/sitecustomize.py` and mounted it via ConfigMap.
  2. Monkey-patched `aiohttp.web.Application.__init__` in `sitecustomize.py` to register custom GET and POST routes upon Application instantiation before the routing table freezes.
  3. Formatted SSE stream responses with explicit event headers and top-level `kind="message"` result payloads.

### Blocker D: 120-Second Timeout via Asyncio Event Loop Deadlock
* **Problem:** To record sessions in the Hermes Web UI Dashboard (`http://localhost:9119`), our `sitecustomize.py` handler forwarded incoming KAgent prompts to Hermes's internal OpenAI-compatible endpoint (`http://127.0.0.1:8080/v1/chat/completions`). Upon testing, requests hung for exactly 2 minutes before failing with `TimeoutError: timed out`.
* **Analysis:** Inside our `async def _a2a_message_handler` route, we initially used Python's standard `urllib.request.urlopen(...)` to query local port 8080. Because `urllib.request` is a synchronous, blocking network library, executing it inside an asynchronous route handler **blocked Python's single-threaded `asyncio` event loop**. While the event loop was frozen waiting for `urlopen` to return, the underlying `aiohttp` server was physically unable to accept or process incoming network connections—including the very connection `urlopen` was attempting to make to `127.0.0.1:8080`! This created a synchronous self-deadlock that timed out after 120 seconds.
* **Remediation:** Replaced synchronous `urllib.request` with an asynchronous HTTP client using **`aiohttp.ClientSession()`**:
  ```python
  async with aiohttp.ClientSession() as session:
      async with session.post(
          "http://127.0.0.1:8080/v1/chat/completions",
          json=req_body,
          headers={"Authorization": f"Bearer {api_key}"},
          timeout=120
      ) as response:
          hermes_resp = await response.json()
  ```
  Because `aiohttp.ClientSession` yields control back to the event loop asynchronously while waiting for network I/O, the server remains unblocked, processes the internal `/v1/chat/completions` request instantly, records the live session in the dashboard, and returns the completion in seconds.

---

## 3. Architectural Adaptation Patterns for 3rd-Party LLMs

When integrating unmodified third-party AI engines (such as Hermes Gateway, Ollama, or vLLM) into an A2A-native harness like `kagent`, cloud-native architectures rely on two established patterns:

### Pattern A: In-Process Adapter via ConfigMap (Implemented as Demo PoC)
* **Architecture:** A startup script (`sitecustomize.py`) is mounted into the container via Kubernetes ConfigMap and added to `PYTHONPATH`. When the Python interpreter boots, the script dynamically attaches A2A protocol routes (`/.well-known/agent-card.json` and `POST /`) directly to the application's existing web server and translates incoming JSON-RPC streams into internal API requests.
* **Maturity & Limitations:** **This implementation is an unfinalized Proof-of-Concept (PoC) built strictly for demo purposes and orchestration validation.** It currently only bridges basic chat prompts and completion responses. It lacks full A2A v0.3 protocol compliance—specifically missing support for streaming token-by-token deltas, tool execution artifacts, multi-part structured payloads, and task cancellation. Attempting complex agentic workflows beyond simple chat messages will result in protocol error logs.
* **Advantages:** Extremely lightweight, zero additional pod containers, zero network latency overhead, and requires zero modifications or forks of the upstream container image.

### Pattern B: Proxy Sidecar Container (Alternative for Production)
* **Architecture:** A dedicated proxy container (e.g., an A2A-to-OpenAI Go or Python proxy) is defined under `extraContainers` in the pod template. The proxy binds to port 8080 to handle KAgent A2A traffic, translates requests, and forwards them over `localhost:8642` to the unmodified AI engine running in the primary container.
* **Advantages:** Complete process and dependency isolation between the protocol adapter and the AI engine, making it ideal for hosting complex, production-grade protocol translators.

---

## 4. High-Level BYO Integration Concepts & Runtime Wrappers

When integrating any third-party or custom AI container into `kagent` BYO mode as a general engineering concept, architects do not modify the underlying container image. Instead, they apply declarative **Runtime Wrappers**:
1. **Security & Execution Wrapper (`podSecurityContext`):** Overrides default container users at runtime (`runAsUser`) to enforce non-root security principles and dynamically grants group write permissions (`fsGroup`) to persistent volumes.
2. **Protocol Adapter Wrapper:** Bridges standard REST/WebSocket AI APIs into Google's A2A streaming JSON-RPC protocol over Server-Sent Events (SSE).

### Four Universal BYO Integration Challenges
1. **Protocol Impedance Mismatch:** Translating A2A JSON-RPC streaming requirements (`message/stream`, `/.well-known/agent-card.json`) to standard AI completions APIs (`/v1/chat/completions`).
2. **Storage Ownership in Unprivileged Containers:** Preventing `Permission denied` runtime crashes when non-root containers attempt to write state or sessions to root-defaulting Kubernetes volume mounts.
3. **Async Event Loop Deadlocks:** Preventing single-threaded async gateways from freezing when protocol adapters make synchronous or blocking HTTP network calls back to the primary web server.
4. **Controller Selector Matching:** Preventing Kubernetes Deployment validation failures by allowing declarative controllers to auto-populate matchLabels rather than hardcoding manual pod selectors.

---

## 5. Validation Logs

The following logs from the active container (`platform-agent-85fbdc464-cp7bt`) confirm clean A2A probe checks, successful async prompt parsing, instant internal LLM completion execution, and SSE streaming:

```text
2026-07-07 10:50:58,120 INFO sitecustomize: [sitecustomize] Successfully added A2A GET and POST routes to web.Application
2026-07-07 10:50:58,125 INFO gateway.platforms.api_server: [Api_Server] API server listening on http://0.0.0.0:8080 (model: hermes-agent)
2026-07-07 10:51:11,644 INFO aiohttp.access: 10.113.133.1 [07/Jul/2026:10:51:11 +0000] "GET /.well-known/agent-card.json HTTP/1.1" 200 681 "-" "kube-probe/1.35"
2026-07-07 10:51:26,644 INFO aiohttp.access: 10.113.133.1 [07/Jul/2026:10:51:26 +0000] "GET /.well-known/agent-card.json HTTP/1.1" 200 681 "-" "kube-probe/1.35"
2026-07-07 10:53:14,210 INFO sitecustomize: [sitecustomize] A2A POST / message received: headers={'Host': 'platform-agent.kube-agents-regular-spike:8080', 'A2a-Version': '0.3', 'Accept': 'text/event-stream'} body={"jsonrpc":"2.0","method":"message/stream","params":{"message":{"parts":[{"kind":"text","text":"do you work?"}]}},"id":"req-101"}
2026-07-07 10:53:14,211 INFO sitecustomize: [sitecustomize] Parsed A2A prompt: 'do you work?' (id=req-101, method=message/stream)
2026-07-07 10:53:16,405 INFO sitecustomize: [sitecustomize] Hermes LLM completion received: 'Yes, I am fully operational and ready to assist you!'
2026-07-07 10:53:16,406 INFO sitecustomize: [sitecustomize] Streaming SSE response for request req-101
2026-07-07 10:53:16,407 INFO aiohttp.access: 10.113.128.3 [07/Jul/2026:10:53:16 +0000] "POST / HTTP/1.1" 200 812 "-" "Go-http-client/1.1"
```
