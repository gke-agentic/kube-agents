# SRE Incident Report: deployment/platform-agent-gateway

### 🚨 Autonomous SRE Declarative Incident Report

- **Component:** `deployment/platform-agent-gateway`
- **Diagnosed Root Cause:** Platform Agent gateway heartbeat timestamp is stale (>600 seconds) due to potential background worker freeze or thread deadlock resulting from LiteLLM downstream API timeouts.
- **Forensic Logs:**
```text
WARNING agent.conversation_loop: Retrying API call... error=Request timed out. WARNING agent.chat_completion_helpers: Stream stale for 240s — no chunks received. Killing connection. WARNING agent.conversation_loop: API call failed error_type=APITimeoutError
```
- **Proposed GitOps Solution:** kubectl rollout restart deployment/platform-agent-gateway -n kubeagents-system

*Human-in-the-loop approval: Please review the LLM-diagnosed manifest changes and merge to deploy.*
