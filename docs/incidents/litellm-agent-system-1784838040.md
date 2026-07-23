# SRE Incident Report: litellm-agent-system

### 🚨 Autonomous SRE Declarative Incident Report

- **Component:** `litellm-agent-system`
- **Diagnosed Root Cause:** The LiteLLM configuration ConfigMap in 'agent-system' defines model 'gemini/gemini-3.5-flash', which downstream clients attempt to query under the model name 'gemini-model' as defined in their '/opt/defaults/config.yaml' templates. Because LiteLLM maps 'model-default' to 'gemini/gemini-3.5-flash' and does not define a model mapped to 'gemini-model', all downstream cron jobs querying 'gemini-model' fail with HTTP 400 Bad Request / 404 Model Not Found errors.
- **Forensic Logs:**
```text
HTTP 400 Bad Request: Invalid model name passed in model=gemini-model. Call /v1/models to view available models for your key.
```
- **Proposed GitOps Solution:** Update 'agent-system/litellm-config' or equivalent templates in the GitOps repository to add a model mapping alias for 'gemini-model' pointing to 'gemini/gemini-3.5-flash', or update 'model' in default configs to use 'model-default' instead of 'gemini-model'.

*Human-in-the-loop approval: Please review the LLM-diagnosed manifest changes and merge to deploy.*
