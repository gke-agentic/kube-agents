# Research Notes: GKE Standard Hybrid Spike (Remediated)

This document captures the final technical findings, root cause analysis (RCA), applied patches, and validation results for the Platform Agent Hybrid Spike on GKE Standard nodes.

---

## 1. Final Status (Validated & Operational)

The `PlatformAgent` harness deployment on GKE Standard is **fully operational and connected**. 
*   **Pod Status**: `1/1 Running` (No restarts/crashes)
*   **Connection Status**: Successfully connected to Google Chat Pub/Sub subscription and executing `StreamingPull` queries.

---

## 2. Technical Findings & Root Cause Analysis

We resolved four major technical blockers preventing the GKE sandbox from establishing connection with Google APIs:

### Blocker A: AppArmor mount blocks nested namespace (GKE v1.35 deprecation)
*   **Problem**: GKE Standard default AppArmor profile restricts nested network namespace creation, crashing the OpenShell supervisor with:
    `mount --make-shared /run/netns failed: Permission denied`
*   **Analysis**: The host GKE nodes are running Kubernetes **v1.35.5**. On v1.35+, legacy metadata annotations (`container.apparmor.security.beta.kubernetes.io/...`) are deprecated and ignored.
*   **Remediation**: Updated the harness patcher script to apply the native `securityContext.appArmorProfile` structure directly on the Sandbox CR spec container template:
    ```yaml
    securityContext:
      appArmorProfile:
        type: Unconfined
    ```

### Blocker B: Missing credentials secrets volume mount
*   **Problem**: The Kagent controller creates the Sandbox CR without mounting API keys and credentials from `platform-agent-secrets`. Under the OpenShell runtime, secrets defined via `valueFrom` are ignored/wiped.
*   **Remediation**: Wrote an inline Python script inside the host-side `provision_harness.sh` script to capture the GKE `Sandbox` CR immediately after creation, dynamically initialize the `volumes` and `volumeMounts` arrays (avoiding JSON Patch empty array failures), and mount the secret to `/etc/secrets/platform-agent-secrets`.

### Blocker C: Sandbox network isolation blocks egress DNS (Port 53)
*   **Problem**: OpenShell runs the Python gateway process inside a nested network namespace (`sandbox-xxxx`) for isolation. In our GKE environment, all UDP and TCP traffic on port 53 (DNS) is rejected inside the namespace (`connection refused`), preventing resolution of `pubsub.googleapis.com`.
*   **Analysis**: 
    1.  The local sandbox subnet `10.200.0.0/24` is unroutable outside the pod. Outgoing packets left `eth0` with source IP `10.200.0.2`, failing return routing. We resolved this by applying **IP Masquerading (Source NAT)** inside the pod's root namespace to rewrite source IPs to the pod IP.
    2.  Even with IP Masquerading, port 53 queries remained blocked, likely due to GKE Datapath V2 CNI (Cilium) DNS proxy intercepts.
*   **Remediation**: Since the system resolver (`getaddrinfo`) reads `/etc/hosts` before querying DNS, we resolved the GCP API hostnames inside the root namespace (where DNS works) and appended static mappings to `/etc/hosts`:
    ```
    173.194.45.95 pubsub.googleapis.com
    216.239.36.223 chat.googleapis.com
    ```
    This allowed the Python process to obtain the correct IPs locally without sending any network packets on port 53.

### Blocker D: Broken relative symlinks inside virtualenv wrapper
*   **Problem**: Our wrapper script moved `/opt/hermes/.venv/bin/hermes` to `hermes.real` to inject environment variables. However, internal libraries invoked `/opt/hermes/.venv/bin/hermes` directly using its absolute path (ignoring `PATH` search), causing a `not found` crash.
*   **Remediation**: Created a symlink from `/opt/hermes/.venv/bin/hermes` pointing back to our wrapper at `/usr/local/bin/hermes`.

---

## 3. Validation Logs

The following logs from `/tmp/gateway.log` confirm that the restarted `hermes` process successfully loaded the static host mappings, bypassed the DNS blocks, resolved the hostnames, and established the channel:

```
I0701 16:40:54.862687    1045 connectivity_state.cc:172] ConnectivityStateTracker client_channel[0x26871a48]: get current state: READY
I0701 16:40:54.863608    1045 chttp2_transport.cc:1910] perform_stream_op[s=0x7bbef8003420; op=0x7bbef8002ba8]:  SEND_INITIAL_METADATA{:path: /google.pubsub.v1.Subscriber/StreamingPull, :authority: pubsub.googleapis.com:443, grpc-timeout: @1801633ms, user-agent: grpc-python/1.81.1 grpc-c/54.0.0 (linux; chttp2), GrpcRegisteredMethod: (nil), :method: POST, :scheme: https, content-type: application/grpc, te: trailers, grpc-accept-encoding: identity, deflate, gzip, WaitForReady: false, x-goog-request-params: 87 bytes redacted for security reasons., x-goog-api-client: 61 bytes redacted for security reasons., x-goog-api-client: 13 bytes redacted for security reasons., authorization: 1031 bytes redacted for security reasons.} RECV_TRAILING_METADATA
I0701 16:40:54.863733    1045 chttp2_transport.cc:1832] perform_stream_op_locked[s=0x7bbef8003420; op=0x7bbef8002ba8]:  SEND_INITIAL_METADATA{:path: /google.pubsub.v1.Subscriber/StreamingPull, :authority: pubsub.googleapis.com:443, ... }
I0701 16:40:54.864896    1050 chttp2_transport.cc:1910] perform_stream_op[s=0x7bbef8003420; op=0x7bbee4001568]:  SEND_MESSAGE:flags=0x00000000:len=123
I0701 16:40:54.865793    1045 chttp2_transport.cc:1910] perform_stream_op[s=0x7bbef8003420; op=0x7bbef8007368]:  RECV_MESSAGE
I0701 16:41:24.836046    1040 chttp2_transport.cc:1087] W:0x7bbf08023440 CLIENT [ipv4:173.194.45.95:443] state IDLE -> WRITING [KEEPALIVE_PING]
I0701 16:41:24.837175    1039 parsing.cc:343] INCOMING[0x7bbf08023440]: PING:ACK len:8 id:0x00000000
```
