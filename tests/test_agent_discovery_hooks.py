"""The repository's own skills are discoverable by the harness a reader opens it in.

    python3 -m unittest discover -s tests -p 'test_*.py'

Stdlib unittest, no pytest, matching the other suites in this directory.

A user who asks an assistant "install kube-agents into my GCP project" gets one
of two things. Either the harness finds `.agents/skills/install-kube-agents/`
and follows a procedure pinned to a release tag, or the model answers from
whatever it absorbed about open-source installers and invents a plausible URL.
#1332 is the second outcome, observed: a fabricated
`gke-labs.github.io/.../install.sh`, the namespace `agent-system`, and a model
version this project has never shipped.

Two mechanisms stand between those outcomes, and nothing checked either one.

The first is where the skills sit. Claude Code reads `.claude/skills/`, and
#1471 made that a symlink to `.agents/` rather than a second copy, which is
also the path Codex and Gemini CLI already read as a project skill directory.
A symlink is one `git mv` away from being an ordinary directory that drifts, or
an absolute path that resolves only on the machine that wrote it, and either
failure is invisible: the repository still contains every skill, so nothing
looks wrong until an assistant in a fresh clone reports it has none.

The second is the frontmatter `description`. Every harness above loads skill
names and descriptions at session start and the full `SKILL.md` only once the
description matches what the user asked for, so on the install path the
description *is* the discovery mechanism -- the body cannot be consulted to
decide whether to consult the body. A description that stops naming the product
or the action a user types turns the skill off without touching a line of it.
`test_lifecycle_skill_descriptions_name_the_product_and_the_action` holds those
three to the words; it is deliberately a floor and not a phrasebook, because a
test that pins whole sentences is a test that gets deleted the first time
someone improves one.

Scope is this repository's own skills, under `.agents/skills/`. The skills
baked into the agent images (`agents/<profile>/skills/`) are loaded by Hermes
from a profile home, not discovered from a checkout, and
`scripts/check_prompt_assets.py` already holds their manifests.
"""

import os
import pathlib
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"

# `read_frontmatter` is the parser `make docs-generate` reads skill metadata
# with. Importing it rather than writing a second one is what keeps this test
# measuring what the tooling sees: a frontmatter block that this file parsed
# but the generator did not would pass here and produce an empty row there.
# `generate_docs` imports `check_docs_map`, its neighbour, so scripts/ goes on
# the path rather than the module's own file.
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import generate_docs  # noqa: E402  (path set above)

#: Directories a harness reads, and the `.agents/` directory each must resolve
#: to. Claude Code is the one that needs them: `.agents/skills/` is already a
#: project skill path for Codex and Gemini CLI, and Jetski reads it directly.
CLAUDE_LINKS = {
    ".claude/skills": ".agents/skills",
    ".claude/rules": ".agents/rules",
}

#: The lowercase words each lifecycle skill's description has to contain: the
#: product a user names, and the action they ask for. A description keeping
#: both can be rewritten freely; one dropping either stops matching the request
#: it exists to serve.
LIFECYCLE_TRIGGERS = {
    "install-kube-agents": ("kube-agents", "install"),
    "uninstall-kube-agents": ("kube-agents", "uninstall"),
    "upgrade-kube-agents": ("kube-agents", "upgrade"),
}


def _skill_dirs():
    return sorted(p for p in SKILLS_DIR.iterdir() if (p / "SKILL.md").is_file())


class ClaudeSkillLinksTest(unittest.TestCase):
    def test_claude_directories_are_symlinks_into_dot_agents(self):
        for link, target in CLAUDE_LINKS.items():
            with self.subTest(link=link):
                path = REPO_ROOT / link
                self.assertTrue(
                    path.is_symlink(),
                    f"{link} is not a symlink; Claude Code would read a second "
                    f"copy of the skills that drifts from {target}",
                )
                self.assertEqual(
                    path.resolve(),
                    (REPO_ROOT / target).resolve(),
                    f"{link} resolves somewhere other than {target}",
                )

    def test_claude_symlinks_are_relative(self):
        # An absolute link records the path of the machine that created it and
        # dangles in every other clone -- including the fresh one an assistant
        # is asked to install from.
        for link in CLAUDE_LINKS:
            with self.subTest(link=link):
                self.assertFalse(
                    os.path.isabs(os.readlink(REPO_ROOT / link)),
                    f"{link} points at an absolute path, so it resolves only "
                    "in the checkout it was made in",
                )


class SkillFrontmatterTest(unittest.TestCase):
    def test_every_skill_declares_a_name_matching_its_directory(self):
        for skill in _skill_dirs():
            with self.subTest(skill=skill.name):
                name = generate_docs.read_frontmatter(skill / "SKILL.md").get("name")
                self.assertEqual(
                    name,
                    skill.name,
                    "a harness loads a skill by directory and cites it by "
                    "frontmatter name; the two disagreeing means the name in "
                    "the catalogue cannot be loaded",
                )

    def test_every_skill_declares_a_description(self):
        for skill in _skill_dirs():
            with self.subTest(skill=skill.name):
                description = generate_docs.read_frontmatter(skill / "SKILL.md").get(
                    "description", ""
                )
                self.assertTrue(
                    description.strip(),
                    "the description is the only part of a skill loaded before "
                    "it is chosen; without one the skill is present and "
                    "unreachable",
                )


class LifecycleSkillDiscoverabilityTest(unittest.TestCase):
    def test_the_lifecycle_skills_are_present(self):
        self.assertLessEqual(
            set(LIFECYCLE_TRIGGERS),
            {skill.name for skill in _skill_dirs()},
            "a lifecycle skill moved or was renamed; the triggers below no "
            "longer guard anything",
        )

    def test_lifecycle_skill_descriptions_name_the_product_and_the_action(self):
        for name, triggers in LIFECYCLE_TRIGGERS.items():
            description = generate_docs.read_frontmatter(
                SKILLS_DIR / name / "SKILL.md"
            ).get("description", "")
            for trigger in triggers:
                with self.subTest(skill=name, trigger=trigger):
                    self.assertIn(
                        trigger,
                        description.lower(),
                        f"{name}'s description no longer contains {trigger!r}, "
                        "so a user asking for it in those words may not reach it",
                    )


if __name__ == "__main__":
    unittest.main()
