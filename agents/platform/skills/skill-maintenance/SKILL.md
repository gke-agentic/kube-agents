---
name: skill-maintenance
description: Audits, tests, and maintains existing agent SKILL.md definitions to prevent instructional drift, ensure trigger precision, and verify token efficiency.
---

# Agent Skill Maintenance Skill

Use this skill to perform continuous quality assurance, structural audits, and proactive maintenance across all `SKILL.md` definitions inside `agents/platform/skills/`. This skill ensures that agent behaviors remain token-efficient, trigger precisely without semantic overlap, and strictly enforce the repository's GitOps boundaries.

## 🔍 Skill Maintenance & Audit Workflow

### Step 1: Frontmatter & Trigger Precision Audit

Agent skills trigger exclusively based on semantic matching against the YAML frontmatter `name` and `description`. Audit all `SKILL.md` frontmatter blocks to eliminate trigger overlap, vague keywords, and missing operational cues.

**Commands:**

```bash
# 1. Extract and list all active skill names and descriptions
find agents/platform/skills -maxdepth 2 -name "SKILL.md" -exec awk 'FNR==1,/^---$/ {if ($0!~/^---$/) print FILENAME ": " $0}' {} +

# 2. Check for missing required frontmatter keys (`name` or `description`)
find agents/platform/skills -maxdepth 2 -name "SKILL.md" -exec awk 'BEGIN {n=0; d=0} /^name:/ {n=1} /^description:/ {d=1} END {if (!n || !d) print FILENAME " -> MISSING FRONTMATTER KEYS"}' {} \;
```

#### Validation Rules:

- **No Trigger Overlap:** Ensure no two skills claim the exact same keywords or operational scenarios (e.g., separating Day 0 design vs. Day 1 verification vs. Day 2 troubleshooting).
- **Specific Triggering Criteria:** `description` must clearly state _when_ to use the skill and _what_ specific tools/resources it acts on. Reject conversational fluff like `"Helpful guide for doing things"` in favor of `"Use when designing ComputeClasses and node selection strategies prior to deployment."`

---

### Step 2: Command & Architecture Verification

Verify that all CLI commands, embedded scripts, and Kubernetes resource queries referenced within `SKILL.md` bodies are valid, executable, and aligned with current `kube-agents` conventions.

**Commands:**

```bash
# 1. Check for deprecated or invalid kubectl/gcloud flags in skill files
grep -rnE "kubectl --|gcloud --" agents/platform/skills/ | grep -E "(--dry-run=true|--no-headers=false|beta container clusters)" || true

# 2. Verify that local script paths referenced in skills (e.g., submit_suggestion.py) actually exist on disk
grep -oE "\./[a-zA-Z0-9_-]+/[a-zA-Z0-9_./-]+\.py" agents/platform/skills/*/SKILL.md | while read -r match; do
  file=$(echo "$match" | cut -d: -f2)
  if [ ! -f "$file" ] && [ ! -f "agents/platform/$file" ]; then
    echo "BROKEN SCRIPT REFERENCE: $match"
  fi
done
```

#### Validation Rules:

- **Up-to-Date API Versions:** Ensure Kubernetes manifests embedded in examples use stable APIs (`cloud.google.com/v1` for `ComputeClass`, `apps/v1` for `Deployment`).
- **Accurate Path References:** Ensure helper script invocations accurately point to active script locations (`./skills/submit-suggestion/scripts/submit_suggestion.py` or `./scripts/github_token_refresh.py <owner>/<repo>`).

---

### Step 3: Token Efficiency & Formatting Standardization

Audit prose density following the exact formatting criteria defined in `skill-review`:

1. **Header Standardization:** Require top-level `# Task` or `# <Title>` and concise secondary headers (`## Workflow`, `## Checks`).
2. **Persona & Fluff Elimination:** Remove conversational setup phrases (`"You are an expert Kubernetes SRE..."`, `"In this guide we will explore..."`). Replace immediately with imperative verbs (`"Inspect..."`, `"Query..."`, `"Verify..."`).
3. **High-Density Bullet Points:** Condense multi-sentence paragraphs into single, high-fidelity bullet points.

**Command to inspect token density and line length:**

```bash
wc -l agents/platform/skills/*/SKILL.md | sort -nr
```

---

### Step 4: GitOps Boundary & Least Privilege Check

Every skill inside `agents/platform/skills/` must strictly uphold the foundational `kube-agents` GitOps boundary (`SOUL.md`).

**Commands:**

```bash
# Search for skills instructing direct live mutations (`kubectl apply`, `kubectl edit`, `kubectl delete`)
grep -rnE "kubectl apply -f|kubectl edit|kubectl delete|gcloud container .* update" agents/platform/skills/ | grep -v "submit-suggestion"
```

#### Validation Rules:

- **No Direct Live Mutations:** If a skill instructs the agent to run `kubectl apply` or `kubectl edit` directly on a production cluster without proposing a GitOps PR patch, flag immediately as a severe boundary violation.
- **Mandatory PR Delegation:** Ensure all remediation workflows terminate with instructions to generate a YAML manifest patch and submit a GitHub Pull Request via `submit-suggestion`.

---

### Step 5: GitOps Remediation for Skill Updates

1. When modifying or optimizing existing `SKILL.md` definitions, **never commit directly to upstream without review**.
2. Stage only the specific `SKILL.md` files adjusted during your maintenance audit.
3. Invoke the **`submit-suggestion`** skill (`./skills/submit-suggestion/scripts/submit_suggestion.py`) to create a clean `platform-agent/skill-maintenance-<target>` branch and Pull Request for human review.
