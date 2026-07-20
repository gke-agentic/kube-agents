# How to contribute

We'd love to accept your patches and contributions to this project.

## Before you begin

### Sign our Contributor License Agreement

Contributions to this project must be accompanied by a
[Contributor License Agreement](https://cla.developers.google.com/about) (CLA).
You (or your employer) retain the copyright to your contribution; this simply
gives us permission to use and redistribute your contributions as part of the
project.

If you or your current employer have already signed the Google CLA (even if it
was for a different project), you probably don't need to do it again.

Visit <https://cla.developers.google.com/> to see your current agreements or to
sign a new one.

### Review our community guidelines

This project follows
[Google's Open Source Community Guidelines](https://opensource.google/conduct/).

## Contribution process

### Code reviews

All submissions, including submissions by project members, require review. We
use GitHub pull requests for this purpose. Consult
[GitHub Help](https://help.github.com/articles/about-pull-requests/) for more
information on using pull requests.

---

## GKE Skill Contribution Workflow

This project manages platform engineering and SRE companion capabilities using modular, declarative agent skills. This section provides a clear, standardized development and integration workflow for contributors who wish to submit new agent skills.

### 1. Skill Folder Structure

All new skills must be structured in a self-contained directory placed under the platform agent's skills tree:

```text
agents/platform/skills/
└── <skill-name>/
    ├── SKILL.md            # Declarative specification (Mandatory)
    ├── README.md           # Developer documentation (Optional)
    └── scripts/            # Helper scripts executed by the skill (Optional)
        └── <script_file>   # E.g., Python, Bash, or Node.js helpers
```

- Keep all executable files inside the skill-local `scripts/` subfolder.
- Do not mix generic tool scripts in the top-level repository folder.

### 2. Standardized `SKILL.md` Format

The `SKILL.md` file serves as the core declarative definition parsed by the OpenClaw and Gemini platforms. It must conform strictly to the following specification:

#### YAML Frontmatter Rules
Every `SKILL.md` must start with a clean, two-key YAML frontmatter block:
```yaml
---
name: <kebab-cased-skill-id>
description: <terse, punchy verb-led description under 20 words>
---
```

#### Markdown Document Sections
Use precise H1 headers without prose paragraphs or conversational AI filler words. Organize content into the following sections:

- **`# Task`**: Explains the precise objective of the skill (e.g. "Audit Kubernetes service account annotations").
- **`# Checks`**: Outlines the validation conditions or steps executed by the agent. Use action-led bullet lists.
- **`# Workflow`**: Defines the precise sequence of CLI or diagnostic tool commands the agent will execute.

Example template:
```markdown
# Task
Audit and enforce Least Privilege access constraints on GKE cluster namespaces.

# Checks
- Verify that default namespace service accounts are not granted cluster-admin access.
- Confirm namespace network policies are configured.

# Workflow
- Run `/scripts/audit_namespaces.sh --target default` to gather current configurations.
- Log anomalies and write findings to `/opt/data/scratch/report.md`.
```

### 3. Validation Workflow

Before submitting your PR, you must run the local static validator script to verify YAML frontmatter integrity, metadata syntax, and directory layout:

```bash
node scripts/validate_skill.cjs agents/platform/skills/<skill-name>
```

Ensure the validation command completes with exit code `0` and displays no warnings or format errors.

### 4. Local Development, Linking & Packaging

To develop and test your skills interactively on a running gateway:

- **Link the skill locally:** Place the skill directory in your workspace and use the developer link tool to load it dynamically into the active runtime:
  ```bash
  gemini skills link agents/platform/skills/<skill-name>
  ```
- **Package into a `.skill` archive:** Once validated and tested, package the skill into a standalone, compressed deployment unit for distribution:
  ```bash
  node scripts/package_skill.cjs agents/platform/skills/<skill-name>
  ```

---

## Pull Request Hygiene

1. Create a branch with a descriptive name linking to the issue (e.g., `fix/issue-123-guidelines`).
2. Follow [Conventional Commits](https://www.conventionalcommits.org/) for your commit messages (e.g., `docs(skills): add skill import workflow and contribution guide`).
3. Push to your fork and submit a Pull Request against the upstream main branch.
