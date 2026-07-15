---
name: skill-debugging
description: Systematic SOP for diagnosing why agent skills fail to trigger, produce incorrect outputs, or encounter execution bottlenecks.
---

# Agent Skill Debugging Skill

Use this skill when an agent skill inside `agents/platform/skills/` fails to trigger during relevant user queries, triggers incorrectly during unrelated tasks, or produces malformed, verbose, or non-compliant output. This skill provides a structured root cause analysis (RCA) and debugging loop for `SKILL.md` instructions.

## 🔍 Skill Debugging Workflow

### Step 1: Trigger Failure Diagnosis (False Negatives & False Positives)

When a skill fails to activate (`False Negative`) or activates when not needed (`False Positive`), isolate the exact semantic triggering bottleneck in the YAML frontmatter.

**Commands:**

```bash
# 1. Inspect the target skill's YAML frontmatter
head -n 10 agents/platform/skills/<target_skill>/SKILL.md

# 2. Check for frontmatter trigger overlap against all other existing skills in the repository
grep -rn "description:" agents/platform/skills/*/SKILL.md
```

#### Diagnostic Decision Tree:

- **False Negative (Skill never triggers when needed):**
  - Check `description` keywords against the exact user prompt phrasing. If `description` uses overly specific jargon (`"Use for v2 alpha quota validation"`) while users ask natural questions (`"Why are my pods pending?"`), add broader operational triggers (`"Use when diagnosing pod scheduling failures, quota rejections, or capacity stockouts."`).
  - Check if `name` is too generic or conflicts with another builtin skill (`debug` vs `gke-self-debugging`).
- **False Positive (Skill triggers during unrelated requests):**
  - Check if `description` contains overly broad words (`"Use for GKE"`, `"Use when running commands"`). Constrain `description` by specifying negative boundaries (`"Use when diagnosing Day 2 compute stockouts. Do not use for initial Day 0 design or simple network drops."`).

---

### Step 2: Context Window & Execution Bottleneck Analysis

When a skill triggers correctly but the agent gets stuck, hallucinates commands, or runs out of context tokens during execution, analyze the internal skill instruction flow.

**Commands:**

```bash
# 1. Check total line count and character volume of the target SKILL.md
wc -l agents/platform/skills/<target_skill>/SKILL.md
wc -c agents/platform/skills/<target_skill>/SKILL.md

# 2. Inspect for unbounded log queries or open-ended instructions
grep -nE "--limit|--tail|cat |gcloud logging read" agents/platform/skills/<target_skill>/SKILL.md
```

#### Diagnostic Checks:

- **Unbounded Log/Event Queries:** If the skill instructs `kubectl logs <pod>` without `--tail=100` or `gcloud logging read` without `--limit=25`, the output will flood the agent's context window, causing truncation or hallucinated reasoning. Ensure every log query mandates a strict `--tail` or `--limit` bound.
- **Instruction Bloat (>300 lines):** If `SKILL.md` exceeds 300 lines of complex branching, split reference material or static templates into a separate `references/` subdirectory and instruct the agent to view those files only when needed (`view_file`).

---

### Step 3: Output Misalignment & Hallucination Diagnosis

When a skill executes but produces output that violates architectural rules (e.g., outputs raw CLI flags, attempts direct live cluster edits, or omits GCP Console links):

**Commands:**

```bash
# 1. Check if the skill clearly defines output formatting expectations and GitOps boundaries
grep -iE "gitops|submit-suggestion|pull request|console\.cloud\.google\.com" agents/platform/skills/<target_skill>/SKILL.md
```

#### Diagnostic Checks:

- **Missing GitOps Delegation:** If the agent attempts `kubectl edit` instead of opening a PR, the `SKILL.md` lacks an explicit `### GitOps Remediation Boundary` section directing the agent to `submit-suggestion`.
- **Missing Telemetry Links:** If the agent fails to output clickable GCP Console links, add the mandatory URL templates (`https://console.cloud.google.com/logs/query...`) directly into the final report step of the skill instructions.

---

### Step 4: Ablation & Regression Verification

Before finalizing a fix for a broken `SKILL.md`:

1. **With vs. Without Test:** Verify how the agent approaches a benchmark user prompt with the current `SKILL.md` instructions versus the revised instructions.
2. **Determinism Check:** Ensure that following the steps in the revised `SKILL.md` produces deterministic, step-by-step diagnostic evidence without ambiguity.
3. **Stage & Submit Suggestion:** Stage only the repaired `SKILL.md` file and invoke `./skills/submit-suggestion/scripts/submit_suggestion.py` to open a Pull Request for human review.
