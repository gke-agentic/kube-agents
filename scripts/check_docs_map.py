#!/usr/bin/env python3
"""Verify that the documentation map inventories every Markdown document.

``docs/README.md`` is the hand-maintained map of every ``.md``/``.mdx`` file in
the repository. Hand-maintained means it drifts: a PR adds, moves, renames, or
deletes a document and forgets the map. This check makes that drift a CI
failure instead of a review-time hope. Two directions:

* **Coverage** -- every git-tracked ``.md``/``.mdx`` file must be matched by at
  least one backticked path (or glob) somewhere in the map's inventory tables
  (section 4). Collapsed family rows use globs (``agents/platform/skills/*/
  SKILL.md``, ``examples/gitops-repo/**``), so one row can cover many files.
  Files inside dot-directories (``.agents/``, ``.github/``, ``.claude/``, …)
  are tooling, not documentation: the map does not inventory them and this
  check does not require them — the map and the check share one scope.
* **Existence** -- every backticked path in the *first column* of an inventory
  row must match at least one tracked file. A path cell that matches nothing is
  a stale row: the file was moved or deleted and the map was not updated.

Deliberately NOT checked: the prose summaries, the directory-tree counts in
section 1, and mentions outside the inventory tables. Those stay on PR review
(the ``review-docs-drift`` skill); this script only guarantees presence.

Standard library only, so it runs in CI and in a bare clone.

Usage::

    python3 scripts/check_docs_map.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAP = REPO / "docs" / "README.md"

# The inventory starts at section 4 and ends at the next ## heading.
INVENTORY_START = "## 4. Inventory"

# Backticked tokens that plausibly denote a doc path or path glob.
TOKEN_RE = re.compile(r"`([^`]+)`")

# Site-page rows are written relative to the site content root (their table
# says so in its header); try this prefix when a token does not resolve as-is.
SITE_PREFIX = "docs/site/src/content/docs/"

# The map does not inventory itself; section 1 declares it ("this map").
SELF = "docs/README.md"


def in_dot_dir(path: str) -> bool:
    """True for paths inside a dot-directory (.agents/, .github/, .claude/, …).

    Those hold tooling artifacts — review skills, PR/issue templates, style
    guides, local agent config — not documentation a reader navigates. They
    are out of the map's scope by the same rule, so new tooling files never
    force a map edit. (A dot-dir path in a path cell would still be validated
    for existence, keeping the two scopes from silently diverging.)
    """
    return any(seg.startswith(".") for seg in path.split("/"))


def tracked_docs() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.md", "*.mdx"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return {p for p in out.stdout.split("\0") if p}


def inventory_rows(text: str) -> list[str]:
    """Return the markdown table rows of the inventory section."""
    try:
        body = text.split(INVENTORY_START, 1)[1]
    except IndexError:
        sys.exit(f"ERROR: {MAP} has no '{INVENTORY_START}' section")
    rows = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        # Skip header-separator rows (|---|---|).
        if set(stripped) <= {"|", "-", ":", " "}:
            continue
        rows.append(stripped)
    return rows


def looks_like_doc_path(token: str) -> bool:
    return (token.endswith((".md", ".mdx")) or token.endswith("/**")) and " " not in token


def pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a map glob to a regex. `**` crosses slashes, `*` does not."""
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches(pattern: str, files: set[str]) -> set[str]:
    hits = set()
    for candidate in (pattern, SITE_PREFIX + pattern):
        rx = pattern_to_regex(candidate)
        hits |= {f for f in files if rx.match(f)}
        if hits:
            break
    return hits


def main() -> int:
    files = tracked_docs()
    files.discard(SELF)
    text = MAP.read_text(encoding="utf-8")
    rows = inventory_rows(text)

    covered: set[str] = set()
    stale: list[tuple[str, str]] = []  # (row path-cell token, reason)

    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        if not cells:
            continue
        # Coverage may come from a token in any cell of the row; existence is
        # only enforced for the path column (the first cell), where every
        # token is a deliberate path claim rather than prose.
        for cell_index, cell in enumerate(cells):
            for token in TOKEN_RE.findall(cell):
                if not looks_like_doc_path(token):
                    continue
                hits = matches(token, files)
                covered |= hits
                if cell_index == 0 and not hits and token != SELF:
                    stale.append((token, "matches no tracked .md/.mdx file"))

    required = {f for f in files if not in_dot_dir(f)}
    missing = sorted(required - covered)

    ok = True
    if missing:
        ok = False
        print(f"{len(missing)} tracked doc(s) missing from the map inventory ({MAP.relative_to(REPO)}):")
        for f in missing:
            print(f"  MISSING  {f}")
    if stale:
        ok = False
        print(f"{len(stale)} stale path(s) in the map's inventory path column:")
        for token, reason in stale:
            print(f"  STALE    `{token}` -- {reason}")
    if ok:
        exempt = len(files) - len(required)
        print(
            f"Documentation map inventory covers all {len(required)} tracked docs; "
            f"no stale path cells ({exempt} dot-directory tooling files exempt from coverage)."
        )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
