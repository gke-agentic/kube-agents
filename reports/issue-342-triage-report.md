# SRE Triage & Architectural Report: Issue #342
**Repository:** `gke-labs/kube-agents`  
**Issue:** [Issue #342: ci: add GitHub Action to verify documentation freshness on code changes](https://github.com/gke-labs/kube-agents/issues/342)  
**Status:** Escalated (`status:escalation-needed`)  
**Blocker:** Permissions / Platform Restrictions (GitHub App lacks `workflows` write scope)

---

## 1. Executive Summary

A comprehensive automated GitHub Actions workflow (`docs-freshness-check.yml`) has been successfully designed, authored, and validated locally using `actionlint`. However, direct deployment via automated git push is blocked because the Platform Agent's GitHub App credentials lack the `workflows` write permission scope required by GitHub to modify `.github/workflows/` files. 

Human SRE intervention is required to commit/push the proposed workflow file or update the GitHub App scopes to unblock automated CI deployments.

---

## 2. Incident & Triage Overview

| Attribute | Value |
| :--- | :--- |
| **Target Issue (Upstream)** | [#342](https://github.com/gke-labs/kube-agents/issues/342) |
| **Component** | CI / Automation |
| **Proposed File** | `.github/workflows/docs-freshness-check.yml` |
| **Diagnostic State** | Blocked by API Scope Constraints |
| **Action Required** | Manual SRE Committal & Approval |

---

## 3. Platform Constraint Evidence (Audit Trace)

During the automated committal attempt of the validated workflow file, the remote Git server rejected the push with the following signature:

```text
To https://github.com/gke-agentic/kube-agents.git
 ! [remote rejected] fix/issue-342-docs-freshness -> fix/issue-342-docs-freshness (refusing to allow a GitHub App to create or update workflow `.github/workflows/docs-freshness-check.yml` without `workflows` permission)
error: failed to push some refs to 'https://github.com/gke-agentic/kube-agents.git'
```

---

## 4. Proposed Solution & File Contents

To prevent documentation drift, the proposed `.github/workflows/docs-freshness-check.yml` workflow checks for changes in core code paths and verifies that documentation updates (`*.md` or `docs/`) were committed alongside. If not, it warns the PR author and prints targeted guidance.

The full, verified file contents are shown below for manual SRE commit:

```yaml
name: Documentation Freshness Check

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read

jobs:
  freshness-check:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v7
        with:
          fetch-depth: 0

      - name: Verify Documentation Freshness
        id: check
        shell: bash
        run: |
          BASE_REF="${{ github.base_ref }}"
          echo "Comparing against origin/${BASE_REF}..."
          
          CHANGED_FILES=$(git diff --name-only origin/${BASE_REF}...HEAD)
          
          HAS_CODE_CHANGES="false"
          HAS_DOC_CHANGES="false"
          CHANGED_CODE_FILES=""
          CHANGED_DOC_FILES=""
          
          while read -r file; do
            if [ -z "$file" ]; then
              continue
            fi
            
            if [[ "$file" =~ ^(k8s-operator/|agents/|deploy/|scripts/|examples/) ]]; then
              HAS_CODE_CHANGES="true"
              CHANGED_CODE_FILES="${CHANGED_CODE_FILES}\n- \`$file\`"
            fi
            
            if [[ "$file" == *.md ]] || [[ "$file" =~ ^docs/ ]]; then
              HAS_DOC_CHANGES="true"
              CHANGED_DOC_FILES="${CHANGED_DOC_FILES}\n- \`$file\`"
            fi
          done <<< "$CHANGED_FILES"
          
          echo "has_code=${HAS_CODE_CHANGES}" >> "$GITHUB_OUTPUT"
          echo "has_docs=${HAS_DOC_CHANGES}" >> "$GITHUB_OUTPUT"
          
          {
            echo "### 📝 Documentation Freshness Audit"
            echo ""
            echo "| Metric | Value |"
            echo "| :--- | :--- |"
            echo "| **Code Files Modified?** | \`${HAS_CODE_CHANGES}\` |"
            echo "| **Documentation Modified?** | \`${HAS_DOC_CHANGES}\` |"
            echo ""
          } >> "$GITHUB_STEP_SUMMARY"
          
          if [ "${HAS_CODE_CHANGES}" = "true" ]; then
            {
              echo "#### 💻 Modified Code Files"
              echo -e "${CHANGED_CODE_FILES}"
              echo ""
            } >> "$GITHUB_STEP_SUMMARY"
          fi
          
          if [ "${HAS_DOC_CHANGES}" = "true" ]; then
            {
              echo "#### 📖 Modified Documentation Files"
              echo -e "${CHANGED_DOC_FILES}"
              echo ""
            } >> "$GITHUB_STEP_SUMMARY"
          fi
          
          if [ "${HAS_CODE_CHANGES}" = "true" ]; then
            if [ "${HAS_DOC_CHANGES}" = "false" ]; then
              echo "::warning::Substantive code changes were detected without any corresponding documentation updates. Please verify if documentation needs freshness alignment!"
              
              {
                echo "⚠️ **Warning: Substantive code changes were detected without any corresponding documentation updates.**"
                echo "Please review if documentation needs to be added or updated for these changes."
                echo ""
                echo "##### 💡 Targeted Freshness Guidance"
              } >> "$GITHUB_STEP_SUMMARY"
              
              while read -r file; do
                if [[ "$file" =~ ^k8s-operator/ ]]; then
                  echo "- 📂 **Operator Changes:** You modified files in \`k8s-operator/\`. If you updated CRDs, APIs, or controllers, please verify if \`k8s-operator/README.md\` or general operator documentation needs updates." >> "$GITHUB_STEP_SUMMARY"
                  break
                fi
              done <<< "$CHANGED_FILES"
              
              while read -r file; do
                if [[ "$file" =~ ^agents/platform/skills/ ]]; then
                  echo "- 📂 **Skill Changes:** You modified files in \`agents/platform/skills/\`. If you updated skill logic or parameters, please ensure corresponding \`SKILL.md\` or the skills import guide is updated." >> "$GITHUB_STEP_SUMMARY"
                  break
                fi
              done <<< "$CHANGED_FILES"
              
              while read -r file; do
                if [[ "$file" =~ ^(scripts/|deploy/) ]]; then
                  echo "- 📂 **Deployment Changes:** You modified files in \`scripts/\` or \`deploy/\`. If installation or provisioning flows changed, please verify if \`INSTALL.md\` or \`README.md\` needs updates." >> "$GITHUB_STEP_SUMMARY"
                  break
                fi
              done <<< "$CHANGED_FILES"
              
              echo "" >> "$GITHUB_STEP_SUMMARY"
            fi
          fi
          
          if [ "${HAS_CODE_CHANGES}" = "false" ] || [ "${HAS_DOC_CHANGES}" = "true" ]; then
            echo "✅ **Documentation is fresh or no code changes were detected.**" >> "$GITHUB_STEP_SUMMARY"
          fi
```

---

## 5. Recommended Remediation Action

To complete the resolution of Issue #342:
1. **Checkout this PR branch** locally:
   ```bash
   git checkout feature/issue-342-docs-freshness-triage
   ```
2. **Commit the workflow file** using your human SRE user credentials (which bypass the app workflow permission limit):
   ```bash
   mkdir -p .github/workflows
   # Save the YAML contents above to .github/workflows/docs-freshness-check.yml
   git add .github/workflows/docs-freshness-check.yml
   git commit -m "ci: add GitHub Action to verify documentation freshness on code changes"
   git push origin feature/issue-342-docs-freshness-triage
   ```
3. **Merge this PR** to deploy the freshness-check workflow automatically across all branches.
