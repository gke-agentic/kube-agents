---
name: review-docs-drift
description: Reviews a pull request for documentation drift — finds which docs the change should have updated, verifies doc claims against source, and checks the docs map and AGENTS.md themselves for staleness.
---

# Task

Given a pull request (a branch diff against `main`), determine whether the repository's documentation is still accurate after the change, and report exactly which documents need updating and why. You are checking two directions:

1. **Code → docs:** the PR changed behavior, names, defaults, paths, or structure that some document states as fact.
2. **Docs → source:** the PR changed documentation, and what it now says must match the source of truth, the repo's documentation rules, and the other docs.

Your two navigation instruments are:

- **`AGENTS.md` (repo root)** — owns the documentation RULES: the canonical-home table (one home per fact), the generated-region rule, link-don't-summarise, no PR-status prose, verify-identifiers-against-source.
- **`docs/README.md`** — the documentation MAP: what lives where, what each document covers, which files carry generated regions and from which sources.

Read both before reviewing the diff.

# Procedure

## 1. Collect the change surface

- `git diff --stat main...HEAD` (or the PR's base) — list every changed file.
- Classify each changed path:
  - **Generated-table source?** `agents/platform/cron/jobs.json`, any `agents/platform/skills/*/SKILL.md` or `agents/cluster/skills/*/SKILL.md` frontmatter, any `k8s-operator/scripts/provision_*.sh` / `teardown_*.sh` banner → the generated regions must be regenerated (`make docs-generate`) and committed.
  - **Identifier source?** `k8s-operator/scripts/common.sh` (SA names, namespace), `k8s-operator/go.mod` (Go version), `agents/platform/config.yaml` / `agents/chat/config.yaml` / `agents/cluster/config.yaml` (toolsets, plugins, MCP servers), `agents/chat/defaults/cron/jobs.json` (the chat profile's script jobs), `agents/platform/SOUL.md` (section numbering, persona rules), `k8s-operator/internal/controller/platformagent_manifests.go` (RBAC bindings, KSA defaults), `k8s-operator/config/rbac/` (controller permissions), `k8s-operator/Makefile` and root `Makefile` (targets), `deploy/docker/Dockerfile` (baked paths) → find every doc that states a fact about the changed item.
  - **Doc file?** → review it under step 3.
  - **Anything else** (controller code, scripts, workflows, examples) → check the map for pages that describe that component.

## 2. Find the affected docs (code → docs)

For each changed source item:

- Look it up in `docs/README.md` to find the pages that document that area; then `git grep` the identifier (old AND new spelling) across `*.md`/`*.mdx` to catch pages the map's summaries don't surface.
- A doc sentence that names a file, flag, default, section number, count, or identifier is a **testable assertion** — test it against the PR's version of the source, not against other docs and not against your memory.
- Pay specific attention to known drift magnets:
  - SA/namespace names and permission-set defaults (`PLATFORM_AGENT_PERMISSION_SET`).
  - `SOUL.md §N` references anywhere in `docs/` — verify against the current `SOUL.md` headings.
  - Paths baked into images (`/opt/defaults/...`) and the Dockerfile COPY sources.
  - Hard-coded counts ("eleven steps", "20 skills") — these should generally not exist; flag any the PR introduces.
  - The Chat Agent / Platform Agent profile split: chat ingress terminates at the `default` profile (`agents/chat/`), the Platform Agent is the `platform` profile reached via kanban delegation. Docs that re-conflate them are drift.
  - The per-cluster Cluster Agents (`agents/cluster/` template, `cluster-*` profiles): read-only, single-cluster, no GitOps write path. Docs that ascribe their scope to the Platform Agent (or grant them write capability) are drift, as is any "two profiles" phrasing that predates them.

## 3. Review changed docs (docs → rules and source)

For every doc the PR adds or edits:

- **Canonical home:** is this fact's home per the `AGENTS.md` table? If the content duplicates another page, it should link instead (the rule is link-don't-summarise; if it must summarise, it must name the canonical page).
- **Generated regions:** nothing inside `<!-- BEGIN GENERATED: ... -->` / `{/* BEGIN GENERATED ... */}` may be hand-edited. If the rendered table is wrong, the fix is in the source + `make docs-generate`.
- **No PR-status prose:** docs describe `main`; "PR #NNN adds/proposes…" sentences rot on merge.
- **Identifiers verified:** every named file/target/SA/version in the new prose exists in the tree at the PR's HEAD.
- **Internal consistency:** the page must not say two different things after the merge (read the whole page, not just the hunk).
- **Deletion audit:** if the PR deletes or trims a doc, confirm every deleted fact genuinely exists at the canonical home the page now points to.

## 4. Check the instruments themselves

- **`docs/README.md` (the map):** if the PR adds, moves, renames, or deletes any doc, the map must reflect it — tree section, counts, and inventory table. The map is hand-maintained and NOT covered by `make docs-check`, so this check is the only guard. Also spot-check that map entries touching the PR's area are still accurate.
- **Map staleness window:** the map stores no "last verified" stamp; derive the delta from git instead — everything that changed since the map itself was last touched is the map's unreviewed backlog:

  ```bash
  git diff --name-status "$(git log -1 --format=%H -- docs/README.md)"..HEAD -- '*.md' '*.mdx'
  ```

  If that list contains adds/renames/deletes the map does not reflect, the map is stale even if this PR didn't cause it — report it either way.

- **`AGENTS.md`:** if the PR changes the repo layout, the docs toolchain (`scripts/generate_docs.py`, checkers in `hack/`/`scripts/`), or where a category of content lives, the layout section and canonical-home table need the same update. If the PR invalidates a rule's example, fix the example.

## 5. Run the mechanical gates

- `make docs-check` at the PR's HEAD — generated tables current, relative links resolve (targets must be git-tracked), terminology matches source.
- If the PR touched a generated-table source: run `make docs-generate` and confirm `git status` is clean afterwards (a dirty tree means the PR forgot to commit regenerated tables).
- `npx prettier --check` on changed `.md`/`.yaml` files (note: the generated `skills/index.mdx` is intentionally prettier-exempt).
- If site pages changed: `cd docs/site && npm run build`.

# Output

Report a triage table: **finding → evidence (file:line + the source that contradicts it) → severity → required action (which doc, what change)**. Separate:

- **Blocking:** a doc now states something false, a generated region is stale or hand-edited, a link is broken, the map/AGENTS.md missed a structural change.
- **Advisory:** style-rule violations (duplication, summarise-without-canonical-link), drift magnets worth a follow-up.

Do not fix silently — the report is the deliverable unless you were explicitly asked to apply fixes. Never resolve a finding by editing a generated region or by making two docs agree with each other without checking the underlying source: source wins, always.
