#!/usr/bin/env python3
"""Render the self-improvement ledger out of a running install.

The ledger is a ConfigMap rather than a file -- `kube-agents-selfimprove-ledger`
in the install's namespace, one `ledger.json` key -- so the read path the design
gives a developer is `kubectl get configmap ... | jq`, and what comes back is
twenty kilobytes of nested JSON in which the questions anyone actually opens it
for are several screens apart. This renders the same document as a report: when
the loop last ran and how many runs are behind it, then the run history, then
the findings ranked worst-first with the gate verdict each would get next run,
then every pull request it has opened.

Read-only and cluster-optional. `--file` takes a ledger somebody has already
pulled down, which is also how the tests exercise every renderer without a
cluster.

The gate column is a simulation, not a record: it replays
`selfimprove_ledger.evaluate_gate` over every finding in the ledger, as if the
next run re-found all of them, using the gate the live CronJob is configured
with. That is the honest answer to "what would happen next hour", and it is
deliberately not the same thing as what any past run decided -- a run only ever
gates the findings it saw that hour.
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import textwrap
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The pure half of the ledger module -- fingerprints, the rolling occurrence
# count, the gate -- imports no Kubernetes client at module scope, which is what
# makes reusing it here possible. Reimplementing `occurrences_in_window` or the
# gate's three conditions would give this tool a second opinion about the same
# ledger, and the two would drift the first time either changed. If the import
# fails (the script copied out of the tree, a branch without the loop) every
# derived column degrades to "?" rather than the whole report failing.
sys.path.insert(0, str(REPO_ROOT / "agents" / "selfimprove" / "scripts"))
try:  # pragma: no cover - the failure branch needs the module absent
    import selfimprove_ledger as ledger_mod
except Exception:  # noqa: BLE001 - any import failure is the same degradation
    ledger_mod = None

DEFAULT_NAMESPACE = "kubeagents-system"
DEFAULT_CONFIGMAP = "kube-agents-selfimprove-ledger"
DEFAULT_CRONJOB = "kube-agents-selfimprove"
LEDGER_KEY = "ledger.json"

#: Mirrors `selfimprove_ledger.LEDGER_MAX_BYTES`, used only on the degraded
#: path above; when the module imports, its value wins and the two cannot
#: disagree.
FALLBACK_MAX_BYTES = 768 * 1024
SEVERITY_ORDER = ("critical", "high", "medium", "low")


# --------------------------------------------------------------------------
# Colour
# --------------------------------------------------------------------------

# SGR colour codes and OSC 8 hyperlink wrappers, both of which occupy no
# columns. Measuring a hyperlinked cell without stripping the OSC sequence
# counts the URL itself as visible text and every border below it misaligns.
_ANSI = re.compile(r"\x1b\[[0-9;]*m|\x1b\]8;[^\x1b]*\x1b\\")

RESET = "\033[0m"
STYLES = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "crit": "\033[1;31m",
    "head": "\033[1;4m",
}

SEVERITY_STYLE = {"critical": "crit", "high": "red", "medium": "yellow", "low": "cyan"}
# Anything not listed is styled as a warning rather than as a success: an
# outcome this tool has never heard of is exactly the one a reader should look
# at, and defaulting it to green would hide it.
OUTCOME_STYLE = {"ok": "green", "killed": "red", "error": "red", "failed": "red"}


class Palette:
    """Applies or discards styles, so no renderer has to know which."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, style: Optional[str]) -> str:
        if not self.enabled or not style or not text:
            return text
        code = STYLES.get(style)
        return "%s%s%s" % (code, text, RESET) if code else text


def want_colour(choice: str, stream=None) -> bool:
    """`--color` plus the two conventions a terminal tool is expected to honour.

    NO_COLOR is checked after the explicit flag, because a flag typed on the
    command line is a stronger statement than a variable inherited from a shell
    profile, and before the TTY test, because its whole point is that a user
    who sets it means it on an interactive terminal too.
    """
    if choice == "always":
        return True
    if choice == "never":
        return False
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    stream = stream if stream is not None else sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


def plain(text: str) -> str:
    """Width-measuring view of a string: what it looks like with colour off."""
    return _ANSI.sub("", text)


_PR_URL = re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)/?$")


def pr_ref(url: str) -> str:
    """`owner/repo#123` for a GitHub pull-request URL, else the URL unchanged.

    A 50-character URL in a table column wraps, and a wrapped URL is no longer
    one a terminal will make clickable or a reader will copy in one go. The
    short form is half the width, and `hyperlink` restores the full link on any
    terminal that supports OSC 8.
    """
    match = _PR_URL.match((url or "").strip())
    return "%s/%s#%s" % match.groups() if match else url


def hyperlink(text: str, url: str, palette: Palette, link_id: str = "") -> str:
    """OSC 8, gated on the same signal as colour.

    Terminals that do not implement it ignore the sequence, but a pipe or a
    file keeps the bytes, so this follows `--color`: that flag already means
    "a human is looking at this in a terminal".

    `link_id` is OSC 8's `id=` parameter, which exists to say that two
    separately-emitted runs are one hyperlink. Anything that wraps across table
    rows needs it: without it a terminal treats each row as its own link and
    highlights only the line under the pointer, and with it the whole location
    lights up as one.
    """
    if not palette.enabled or not url:
        return text
    return "\x1b]8;%s;%s\x1b\\%s\x1b]8;;\x1b\\" % ("id=%s" % link_id if link_id else "", url, text)


#: A URL anywhere in a string, stripped before locations are parsed so that the
#: `github.com` in one is not mistaken for a file called `com`.
_URL_ANYWHERE = re.compile(r"https?://\S+")

#: Either a dotted path with an optional `:line` or `:line-line`, or a bare
#: `:line` continuing the path before it. The extension must start with a
#: letter: that is what keeps `v2026.8.13` and `1.5s` out without a list of
#: known suffixes.
_LOCATION_REF = re.compile(
    r"(?P<path>(?:[\w.+-]+/)*[\w.+-]+\.[A-Za-z]\w*)(?::(?P<line>\d+(?:-\d+)?))?"
    r"|(?<![\w.]):(?P<bare>\d+(?:-\d+)?)"
)


def repo_toplevel(root: Optional[str] = None) -> frozenset:
    """Top-level entries of the kube-agents checkout this script ships in.

    This set is what tells a repo-relative path from everything else a location
    string contains, and it has to be derived rather than listed. The live
    ledger holds a finding in `agent/anthropic_adapter.py`, which is the Hermes
    harness and not this repository at all; linking it to a kube-agents blob URL
    would send the reader to a 404 that looks like the finding is stale rather
    than like the link is wrong. Deriving the set also means a new top-level
    directory needs no edit here.

    Empty when the directory is not a kube-agents checkout, which switches every
    file link off rather than guessing.
    """
    base = REPO_ROOT if root is None else pathlib.Path(root)
    if not (base / "AGENTS.md").is_file():
        return frozenset()
    try:
        return frozenset(entry.name for entry in base.iterdir() if entry.name != ".git")
    except OSError:
        return frozenset()


def location_refs(location: str, roots: frozenset) -> List[Tuple[str, Optional[str]]]:
    """The `path:line` references a location string names, in order, deduped.

    A location is whatever the investigating agent wrote. Most are a bare
    `path:line`, but the ones that are not run to prose -- a parenthetical after
    the path, then a second reference given as a bare `:1162` -- so a bare line
    number attaches to the path before it.

    A candidate whose first segment is not a top-level entry of this repository
    is dropped. That single rule does two jobs: it rejects the things that only
    look like paths (`e.g.` parses as a file named `e` with extension `g`) and
    it rejects real paths in other repositories.
    """
    if not roots:
        return []
    refs: List[Tuple[str, Optional[str]]] = []
    current: Optional[str] = None
    for match in _LOCATION_REF.finditer(_URL_ANYWHERE.sub(" ", location or "")):
        path, line, bare = match.group("path"), match.group("line"), match.group("bare")
        if path:
            if path.split("/")[0] not in roots:
                if "/" in path:
                    # A real path, in another repository. Forget the running
                    # one, so a trailing `:120` is not attached to whatever
                    # repo-relative path came before *that*.
                    current = None
                # Without a slash it is far more likely to be code than a path
                # -- `r.Status()` in a backticked snippet parses as one -- and
                # letting that clear the running path costs the `:1162` that
                # follows it a link it should have had.
                continue
            current = path
            refs.append((path, line))
        elif bare and current:
            refs.append((current, bare))
    seen = set()
    return [ref for ref in refs if not (ref in seen or seen.add(ref))]


def blob_url(repo: str, revision: str, path: str, line: Optional[str] = None) -> str:
    """GitHub's permalink for `path` at `revision`, anchored on `line`.

    Pinned to the revision the finding was made against rather than to a branch:
    the line number is only meaningful against the code the agent read, and a
    branch link drifts out from under it on the next commit.
    """
    if not repo or not revision or not path:
        return ""
    url = "https://github.com/%s/blob/%s/%s" % (repo, revision, path)
    if not line:
        return url
    # GitHub spells a range `#L10-L20`, with the `L` repeated; a location writes
    # it `10-20`.
    return url + "#L%s" % line.replace("-", "-L")


def location_links(
    entry: Dict[str, Any], repo: str, roots: frozenset
) -> List[Tuple[str, str]]:
    """`(label, url)` for every file reference in a finding's location."""
    revision = str(entry.get("revision") or "")
    links = []
    for path, line in location_refs(str(entry.get("location") or ""), roots):
        url = blob_url(repo, revision, path, line)
        if url:
            links.append(("%s:%s" % (path, line) if line else path, url))
    return links


def target_repo(env: Dict[str, str]) -> str:
    """The `owner/name` a finding's revision can be resolved against.

    The fork in fork mode, because that is where the revision the runner checked
    out is guaranteed to exist -- upstream may not have the branch yet. Under
    report-only nothing is pushed anywhere, so the upstream repository is the
    only honest answer.
    """
    if env.get("SELFIMPROVE_MODE") == "report-only":
        return env.get("SELFIMPROVE_UPSTREAM_REPO", "") or ""
    return env.get("SELFIMPROVE_FORK_REPO") or env.get("SELFIMPROVE_UPSTREAM_REPO") or ""


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------

BOX_UNICODE = {
    "h": "─", "v": "│",
    "tl": "┌", "tm": "┬", "tr": "┐",
    "ml": "├", "mm": "┼", "mr": "┤",
    "bl": "└", "bm": "┴", "br": "┘",
}
BOX_ASCII = {
    "h": "-", "v": "|",
    "tl": "+", "tm": "+", "tr": "+",
    "ml": "+", "mm": "+", "mr": "+",
    "bl": "+", "bm": "+", "br": "+",
}


class Column:
    """One column.

    `wrap` marks a column that gives up width first, down to `min_width`.
    `expendable` is the next concession after that: a positive value means the
    column may be dropped entirely on a terminal too narrow to hold the table
    even at its minimums, highest value first. Zero -- the default -- means the
    column is load-bearing and the table runs wide instead.
    """

    def __init__(
        self,
        title: str,
        align: str = "l",
        wrap: bool = False,
        min_width: int = 12,
        expendable: int = 0,
    ) -> None:
        self.title = title
        self.align = align
        self.wrap = wrap
        self.min_width = min_width
        self.expendable = expendable


def _pad(text: str, width: int, align: str) -> str:
    gap = max(0, width - len(plain(text)))
    if align == "r":
        return " " * gap + text
    if align == "c":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def _cell_lines(text: str, width: int) -> List[Tuple[str, int]]:
    """Wrap one cell to `width`, as `(line, source paragraph index)` pairs.

    Deliberate newlines are preserved, and the paragraph index rides along so a
    cell that stacks several facts -- a finding's title, its location, its gate
    verdict -- can colour each one differently even after wrapping has turned
    them into an indeterminate number of lines.

    `break_long_words` is on because the cells most likely to overflow are file
    paths and fingerprints, which have no spaces to break at -- left unbroken
    they push the column past its allotment and every border below misaligns.
    """
    out: List[Tuple[str, int]] = []
    for index, para in enumerate((text or "").split("\n")):
        if not para:
            out.append(("", index))
            continue
        for line in (
            textwrap.wrap(para, width=max(1, width), break_long_words=True, break_on_hyphens=False)
            or [""]
        ):
            out.append((line, index))
    return out or [("", 0)]


def _natural_widths(columns: Sequence[Column], rows: Sequence[Sequence[Sequence[Any]]]) -> List[int]:
    """The width each column would take if nothing had to give."""
    natural = []
    for index, column in enumerate(columns):
        widest = len(plain(column.title))
        for row in rows:
            text = row[index][0] if index < len(row) else ""
            for line in str(text).split("\n"):
                widest = max(widest, len(plain(line)))
        natural.append(widest)
    return natural


def _overhead(count: int) -> int:
    """Borders and padding: `| ` before each cell and ` |` after the last."""
    return 3 * count + 1


def _minimum_width(columns: Sequence[Column], rows: Sequence[Sequence[Sequence[Any]]]) -> int:
    """The narrowest this table can be drawn without dropping a column."""
    natural = _natural_widths(columns, rows)
    return _overhead(len(columns)) + sum(
        column.min_width if column.wrap else natural[index]
        for index, column in enumerate(columns)
    )


def _fit_columns(
    columns: Sequence[Column], rows: Sequence[Sequence[Sequence[Any]]], total: int
) -> Tuple[List[Column], List[List[Sequence[Any]]], List[str]]:
    """Drop expendable columns until the table fits, worst-value first.

    An eighty-column terminal cannot hold the findings table: nine columns of
    borders alone are twenty-eight characters, and the columns that carry the
    finding itself want another eighty. Left to run wide the terminal hard-wraps
    every row and the result is less readable than the JSON this replaces. So
    the least load-bearing columns come out first, and the caller is told which
    -- a table that silently drops a column is a table that lies about what the
    ledger holds.
    """
    kept = list(columns)
    trimmed = [list(row) for row in rows]
    dropped: List[str] = []
    while _minimum_width(kept, trimmed) > total:
        candidates = [i for i, column in enumerate(kept) if column.expendable > 0]
        if not candidates:
            break
        victim = max(candidates, key=lambda i: (kept[i].expendable, i))
        dropped.append(kept[victim].title)
        kept.pop(victim)
        for row in trimmed:
            if victim < len(row):
                row.pop(victim)
    return kept, trimmed, dropped


def _resolve_widths(columns: Sequence[Column], rows: Sequence[Sequence[Sequence[Any]]], total: int) -> List[int]:
    natural = _natural_widths(columns, rows)

    overhead = _overhead(len(columns))
    available = max(total - overhead, 10)
    if sum(natural) <= available:
        return natural

    flex = [i for i, c in enumerate(columns) if c.wrap]
    if not flex:
        return natural

    fixed = sum(w for i, w in enumerate(natural) if i not in flex)
    room = available - fixed
    floor = sum(columns[i].min_width for i in flex)
    if room < floor:
        # Nothing left to give. Honour the minimums and let the table run wide:
        # a table one column too wide is legible, a table with three-character
        # title cells is not.
        return [columns[i].min_width if i in flex else natural[i] for i in range(len(columns))]

    share = float(sum(natural[i] for i in flex)) or 1.0
    widths = list(natural)
    for i in flex:
        widths[i] = max(columns[i].min_width, int(room * (natural[i] / share)))
    # Integer division loses a column or two of the budget; hand the remainder
    # to the widest flexible column rather than leaving the table short.
    drift = room - sum(widths[i] for i in flex)
    if drift > 0:
        widths[max(flex, key=lambda i: widths[i])] += drift
    return widths


def render_table(
    columns: Sequence[Column],
    rows: Sequence[Sequence[Sequence[Any]]],
    palette: Palette,
    width: int,
    box: Dict[str, str],
) -> List[str]:
    """Render `rows` into a bordered table.

    A cell is a tuple of up to five parts: the text, a style for all of it, a
    URL to hyperlink it with, a `{paragraph index: style}` override for a cell
    whose newline-separated parts want colouring individually, and a
    `{paragraph index: URL}` for one that wants them linked individually.

    The two URL forms differ in what has to be unwrapped for the link to be
    drawn -- the whole cell for the plain one, only the paragraph itself for the
    per-paragraph one. A cell stacking a title over a location has no single-line
    form to reach, so a whole-cell URL on it would never render at all.
    """
    columns, rows, dropped = _fit_columns(columns, rows, width)
    widths = _resolve_widths(columns, rows, width)

    def rule(left: str, mid: str, right: str) -> str:
        return palette(left + mid.join(box["h"] * (w + 2) for w in widths) + right, "dim")

    vertical = palette(box["v"], "dim")

    # Distinguishes one wrapped link from another, so that two locations in the
    # same table are never fused into one hyperlink by a shared `id=`.
    link_seq = [0]

    def emit(cells: Sequence[Sequence[Any]]) -> List[str]:
        wrapped = [
            _cell_lines(str(cells[i][0]) if i < len(cells) else "", widths[i])
            for i in range(len(columns))
        ]
        height = max(len(w) for w in wrapped)
        # How many lines each paragraph of each cell ended up occupying, which
        # is what decides whether a per-paragraph link needs an `id=` to hold
        # its pieces together.
        spans = [collections.Counter(para for _, para in w) for w in wrapped]
        link_seq[0] += 1
        row_seq = link_seq[0]
        lines = []
        for line_no in range(height):
            pieces = []
            for i, column in enumerate(columns):
                raw, para = wrapped[i][line_no] if line_no < len(wrapped[i]) else ("", -1)
                cell = cells[i] if i < len(cells) else ("",)
                style = cell[1] if len(cell) > 1 else None
                url = cell[2] if len(cell) > 2 else None
                per_line = cell[3] if len(cell) > 3 else None
                per_line_url = cell[4] if len(cell) > 4 else None
                if per_line and para in per_line:
                    style = per_line[para]
                # A whole-cell URL is drawn only on an unwrapped cell, because
                # it has no way to say which of several paragraphs it belongs
                # to. A blank line is never linked either -- the padding
                # beneath a short cell, which a taller neighbouring column
                # produces on nearly every row, would otherwise carry a
                # zero-width link with nothing for a reader to click.
                linkable = bool(url) and len(wrapped[i]) == 1
                link_id = ""
                if per_line_url and para in per_line_url:
                    url = per_line_url[para]
                    # A per-paragraph link is drawn even when its paragraph
                    # wraps, joined across the rows by `id=`. Dropping it
                    # instead cost the location column every link it had at any
                    # normal terminal width: a path with a line number needs
                    # around 120 columns of FINDING to fit on one line, so an
                    # 80-column terminal rendered no file links at all.
                    linkable = True
                    if spans[i][para] > 1:
                        link_id = "%d.%d.%d" % (row_seq, i, para)
                rendered = palette(raw, style)
                if url and raw.strip() and linkable:
                    rendered = hyperlink(rendered, url, palette, link_id)
                pieces.append(_pad(rendered, widths[i], column.align))
            lines.append(vertical + " " + (" " + vertical + " ").join(pieces) + " " + vertical)
        return lines

    out = [rule(box["tl"], box["tm"], box["tr"])]
    out.extend(emit([(c.title, "head") for c in columns]))
    out.append(rule(box["ml"], box["mm"], box["mr"]))
    for row in rows:
        out.extend(emit(row))
    out.append(rule(box["bl"], box["bm"], box["br"]))
    if dropped:
        out.append(
            palette(
                "  %s dropped to fit %d columns; --width for a wider table"
                % (", ".join(dropped), width),
                "dim",
            )
        )
    return out


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


def parse_iso(text: Any) -> Optional[_dt.datetime]:
    if not isinstance(text, str) or not text.strip():
        return None
    raw = text.strip().replace("Z", "+00:00")
    try:
        when = _dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=_dt.timezone.utc)


def humanise_delta(seconds: float) -> str:
    seconds = abs(int(seconds))
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm" % (seconds // 60)
    if seconds < 86400:
        hours, minutes = divmod(seconds // 60, 60)
        return "%dh%02dm" % (hours, minutes) if minutes else "%dh" % hours
    days, hours = divmod(seconds // 3600, 24)
    return "%dd%dh" % (days, hours) if hours else "%dd" % days


def ago(when: Optional[_dt.datetime], now: _dt.datetime) -> str:
    if when is None:
        return "never"
    delta = (now - when).total_seconds()
    return "in %s" % humanise_delta(delta) if delta < 0 else "%s ago" % humanise_delta(delta)


def stamp(when: Optional[_dt.datetime], utc: bool) -> str:
    """A wall-clock time a reader can compare against their own logs.

    Local by default with the zone spelled out, because the question this
    answers is almost always "did that happen while I was looking at it".
    """
    if when is None:
        return "-"
    if utc:
        return when.astimezone(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    local = when.astimezone()
    # %-I is a glibc/BSD extension, which covers Linux and macOS; the zero-pad
    # fallback keeps this from raising anywhere else.
    try:
        rendered = local.strftime("%Y-%m-%d %-I:%M %p %Z")
    except ValueError:  # pragma: no cover - platform-dependent
        rendered = local.strftime("%Y-%m-%d %I:%M %p %Z")
    return rendered.replace("AM", "am").replace("PM", "pm")


def compact_count(value: int) -> str:
    if value < 1000:
        return str(value)
    if value < 1000000:
        return "%.1fk" % (value / 1000.0)
    return "%.1fM" % (value / 1000000.0)


def clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def meter(fraction: float, cells: int = 18) -> str:
    fraction = min(max(fraction, 0.0), 1.0)
    filled = int(round(fraction * cells))
    return "█" * filled + "░" * (cells - filled)


def short_rev(revision: Any) -> str:
    text = str(revision or "").strip()
    return text[:7] if text else "-"


# --------------------------------------------------------------------------
# Cluster reads
# --------------------------------------------------------------------------


class LoadError(RuntimeError):
    pass


def kubectl_json(args: Sequence[str], context: Optional[str], timeout: int = 30) -> Dict[str, Any]:
    cmd = ["kubectl"]
    if context:
        cmd += ["--context", context]
    cmd += list(args) + ["-o", "json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise LoadError("kubectl is not on PATH; pass --file to read a ledger you already have") from exc
    except subprocess.TimeoutExpired as exc:
        raise LoadError("kubectl timed out after %ds: %s" % (timeout, " ".join(cmd))) from exc
    if proc.returncode != 0:
        raise LoadError((proc.stderr or proc.stdout or "kubectl failed").strip())
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise LoadError("kubectl returned output that is not JSON: %s" % proc.stdout[:200]) from exc


def current_context(context: Optional[str]) -> str:
    if context:
        return context
    try:
        proc = subprocess.run(
            ["kubectl", "config", "current-context"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return "-"
    return proc.stdout.strip() if proc.returncode == 0 else "-"


def load_from_cluster(namespace: str, name: str, context: Optional[str]) -> Tuple[Dict[str, Any], str]:
    cm = kubectl_json(["-n", namespace, "get", "configmap", name], context)
    raw = (cm.get("data") or {}).get(LEDGER_KEY)
    if raw is None:
        raise LoadError(
            "ConfigMap %s/%s has no %r key. The chart renders it empty and the first run fills it in, "
            "so this is a loop that has not completed a run yet." % (namespace, name, LEDGER_KEY)
        )
    return json.loads(raw), raw


def load_from_file(path: str) -> Tuple[Dict[str, Any], str]:
    raw = sys.stdin.read() if path == "-" else pathlib.Path(path).read_text(encoding="utf-8")
    document = json.loads(raw)
    # Accepts either the ledger itself or the whole ConfigMap, because both are
    # things a person ends up with in a file: `kubectl get cm -o json > x.json`
    # is the shorter command and the likelier one.
    if isinstance(document, dict) and isinstance(document.get("data"), dict):
        inner = document["data"].get(LEDGER_KEY)
        if isinstance(inner, str):
            return json.loads(inner), inner
    return document, raw


def load_cronjob(namespace: str, name: str, context: Optional[str]) -> Optional[Dict[str, Any]]:
    """The CronJob carries the gate and the mode, and it may not exist.

    None is a normal answer -- the loop is off, or `--file` was used -- and
    every consumer treats it as "say nothing about the gate" rather than as an
    error.
    """
    try:
        return kubectl_json(["-n", namespace, "get", "cronjob", name], context)
    except LoadError:
        return None


def cronjob_env(cronjob: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not cronjob:
        return {}
    try:
        containers = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    except (KeyError, TypeError):
        return {}
    env: Dict[str, str] = {}
    for container in containers or []:
        for item in container.get("env") or []:
            if isinstance(item, dict) and "name" in item and "value" in item:
                env.setdefault(str(item["name"]), str(item["value"]))
    return env


# --------------------------------------------------------------------------
# Derived views of the ledger
# --------------------------------------------------------------------------


def sorted_findings(ledger: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = ledger.get("findings")
    entries = list(findings.values()) if isinstance(findings, dict) else list(findings or [])
    return [e for e in entries if isinstance(e, dict)]


def severity_rank(entry: Dict[str, Any]) -> int:
    severity = str(entry.get("severity", "")).lower()
    return SEVERITY_ORDER.index(severity) if severity in SEVERITY_ORDER else len(SEVERITY_ORDER)


def occurrences(entry: Dict[str, Any], now: _dt.datetime) -> Optional[int]:
    if ledger_mod is None:
        return None
    return ledger_mod.occurrences_in_window(entry, now)


def reported(entry: Dict[str, Any], now: _dt.datetime) -> Optional[int]:
    if ledger_mod is None:
        return None
    return ledger_mod.reported_occurrences_in_window(entry, now)


def gate_verdicts(ledger: Dict[str, Any], gate: Dict[str, Any], now: _dt.datetime) -> Dict[str, str]:
    """`evaluate_gate` replayed over the whole ledger. See the module docstring."""
    if ledger_mod is None or not gate:
        return {}
    findings = ledger.get("findings")
    if not isinstance(findings, dict):
        return {}
    order = sorted(findings.items(), key=lambda kv: (severity_rank(kv[1]), kv[0]))
    try:
        _, reasons = ledger_mod.evaluate_gate(
            {"findings": findings, "runs": ledger.get("runs", [])},
            gate,
            [fp for fp, _ in order],
            now,
        )
    except Exception:  # noqa: BLE001 - a viewer must not fail on a malformed gate
        return {}
    return reasons


def verdict_style(verdict: str) -> str:
    if verdict.startswith("promoted"):
        return "green"
    if "refused" in verdict:
        return "magenta"
    return "yellow"


def parse_gate(env: Dict[str, str]) -> Dict[str, Any]:
    try:
        gate = json.loads(env.get("SELFIMPROVE_GATE", "") or "{}")
    except ValueError:
        return {}
    return gate if isinstance(gate, dict) else {}


def collect_promotions(entries: Sequence[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Every promotion in the ledger, newest first, paired with its finding.

    Every one rather than the latest per finding: a finding filed twice has two
    pull requests against it, and the second is the more interesting of the two
    -- either the first was closed unmerged or the cooldown lapsed with the
    finding still live. A promotion with no URL is kept and rendered as such,
    because that is `record_promotion(confirmed=False)`: a filing turn that
    charged the budget without printing a link, which is precisely the row
    somebody has to go and look for by hand.
    """
    pairs = [
        (promotion, entry)
        for entry in entries
        for promotion in (entry.get("promotions") or [])
        if isinstance(promotion, dict)
    ]
    pairs.sort(key=lambda pair: str(pair[0].get("at") or ""), reverse=True)
    return pairs


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def render_header(
    ledger: Dict[str, Any],
    raw: str,
    source: str,
    namespace: str,
    name: str,
    cronjob: Optional[Dict[str, Any]],
    env: Dict[str, str],
    gate: Dict[str, Any],
    now: _dt.datetime,
    palette: Palette,
    utc: bool,
) -> List[str]:
    runs = [r for r in (ledger.get("runs") or []) if isinstance(r, dict)]
    entries = sorted_findings(ledger)
    last = runs[-1] if runs else None
    last_at = parse_iso(last.get("at")) if last else None
    outcome = str(last.get("outcome", "?")) if last else "-"

    # The lead line, deliberately: the first question anyone opens the ledger
    # with is whether the loop is still running, and the second is how much
    # history is behind what follows.
    lead = "%s  %s  %s" % (
        palette("last run", "dim"),
        palette(stamp(last_at, utc) if last_at else "never", "bold"),
        palette("(%s)" % ago(last_at, now), "dim"),
    )
    if last:
        lead += "  %s %s" % (
            palette("·", "dim"),
            palette(outcome, OUTCOME_STYLE.get(outcome.lower(), "yellow")),
        )
    lead += "  %s %s" % (
        palette("·", "dim"),
        palette("%d run%s recorded" % (len(runs), "" if len(runs) == 1 else "s"), "bold"),
    )

    lines = [lead, ""]

    def field(label: str, value: str) -> str:
        return "  %s %s" % (palette(label.ljust(10), "dim"), value)

    filed = collect_promotions(entries)
    lines.append(
        field(
            "findings",
            "%d in the ledger  %s"
            % (len(entries), palette("· %d pull request(s) opened all time" % len(filed), "dim")),
        )
    )
    lines.append(field("source", source))
    if source != "file":
        lines.append(field("configmap", "%s/%s" % (namespace, name)))

    mode = env.get("SELFIMPROVE_MODE")
    if mode:
        target = env.get("SELFIMPROVE_FORK_REPO") or env.get("SELFIMPROVE_UPSTREAM_REPO") or ""
        base = env.get("SELFIMPROVE_BASE_BRANCH") or ""
        detail = ""
        if mode != "report-only" and target:
            shown = hyperlink(target, "https://github.com/%s" % target, palette)
            detail = " → %s%s" % (shown, " (base %s)" % base if base else "")
        lines.append(field("mode", palette(mode, "bold") + detail))

    if cronjob:
        schedule = str((cronjob.get("spec") or {}).get("schedule") or "?")
        suspended = bool((cronjob.get("spec") or {}).get("suspend"))
        state = palette("SUSPENDED", "red") if suspended else palette("active", "green")
        scheduled_at = parse_iso((cronjob.get("status") or {}).get("lastScheduleTime"))
        lines.append(
            field(
                "schedule",
                "%s  %s  %s"
                % (schedule, state, palette("last scheduled %s" % ago(scheduled_at, now), "dim")),
            )
        )

    if gate:
        budget = gate.get("maxPullRequestsPerDay", 0)
        spent = ledger_mod.promotions_today(ledger, now) if ledger_mod else None
        cooldown = gate.get("cooldownHours", "?")
        used = "%s of %s" % (spent if spent is not None else "?", budget)
        style = "yellow" if (spent is not None and budget and spent >= budget) else None
        lines.append(
            field(
                "budget",
                "%s pull requests in the last 24h  %s"
                % (palette(used, style), palette("· %sh cooldown" % cooldown, "dim")),
            )
        )

    cap = ledger_mod.LEDGER_MAX_BYTES if ledger_mod else FALLBACK_MAX_BYTES
    size = len(raw.encode("utf-8"))
    fraction = size / float(cap)
    size_style = "red" if fraction > 0.9 else ("yellow" if fraction > 0.7 else "dim")
    lines.append(
        field(
            "size",
            "%s %s"
            % (
                palette(meter(fraction), size_style),
                palette(
                    "%.1f KiB of %d KiB (%.1f%%)" % (size / 1024.0, cap // 1024, fraction * 100),
                    "dim",
                ),
            ),
        )
    )
    return lines


def render_runs(
    ledger: Dict[str, Any],
    limit: int,
    now: _dt.datetime,
    palette: Palette,
    width: int,
    box: Dict[str, str],
    utc: bool,
) -> List[str]:
    runs = [r for r in (ledger.get("runs") or []) if isinstance(r, dict)]
    if not runs:
        return [palette("  no runs recorded yet", "dim")]
    shown = runs[-limit:] if limit > 0 else runs
    # NOTE goes first on a narrow terminal because it is empty on almost every
    # run, then REVISION, then the absolute WHEN -- AGE answers the same
    # question in a third of the width, and "how long ago" is the question a
    # run history is usually being scanned for.
    columns = [
        Column("WHEN", expendable=1),
        Column("AGE", align="r"),
        Column("OUTCOME"),
        Column("FOUND", align="r"),
        Column("PROMOTED", align="r"),
        Column("FILED", align="r"),
        Column("REVISION", expendable=2),
        Column("NOTE", wrap=True, min_width=14, expendable=3),
    ]
    rows = []
    for run in reversed(shown):
        at = parse_iso(run.get("at"))
        outcome = str(run.get("outcome", "?"))
        rows.append(
            [
                (stamp(at, utc), None),
                (ago(at, now), "dim"),
                (outcome, OUTCOME_STYLE.get(outcome.lower(), "yellow")),
                (str(run.get("findings", 0)), None),
                (str(run.get("promoted", 0)), "green" if run.get("promoted") else "dim"),
                (str(run.get("filed", 0)), "green" if run.get("filed") else "dim"),
                (short_rev(run.get("revision")), "dim"),
                (str(run.get("note") or ""), "dim"),
            ]
        )
    out = render_table(columns, rows, palette, width, box)
    if limit > 0 and len(runs) > limit:
        out.append(palette("  %d older run(s) not shown; --runs 0 for all" % (len(runs) - limit), "dim"))
    return out


def render_findings(
    ledger: Dict[str, Any],
    verdicts: Dict[str, str],
    now: _dt.datetime,
    palette: Palette,
    width: int,
    box: Dict[str, str],
    sort: str,
    min_severity: Optional[str],
    signal: Optional[str],
    repo: str = "",
    roots: frozenset = frozenset(),
) -> Tuple[List[str], List[Dict[str, Any]]]:
    entries = sorted_findings(ledger)
    if min_severity:
        ceiling = SEVERITY_ORDER.index(min_severity)
        entries = [e for e in entries if severity_rank(e) <= ceiling]
    if signal:
        entries = [e for e in entries if str(e.get("signal", "")).lower() == signal.lower()]

    if sort == "seen":
        entries.sort(key=lambda e: -(occurrences(e, now) or 0))
    elif sort == "last":
        entries.sort(key=lambda e: str(e.get("last_seen", "")), reverse=True)
    elif sort == "first":
        entries.sort(key=lambda e: str(e.get("first_seen", "")))
    else:
        entries.sort(key=lambda e: (severity_rank(e), -(occurrences(e, now) or 0)))

    if not entries:
        return [palette("  no findings match", "dim")], entries

    # What a narrow terminal loses, in order. REPORTED is the agent's own
    # untrusted number and never gates anything; CONF is one word; SIGNAL is
    # recoverable from the finding text. SEVERITY, SEEN and PRS stay because
    # they are what the table is sorted and scanned by, and FINDING stays
    # because without it there is no table. `--detail` still has all of it.
    columns = [
        Column("#", align="r"),
        Column("SEVERITY"),
        Column("SIGNAL", expendable=1),
        Column("CONF", expendable=2),
        Column("SEEN", align="r"),
        Column("REPORTED", align="r", expendable=3),
        Column("PRS", align="r"),
        Column("LAST", align="r"),
        Column("FINDING", wrap=True, min_width=28),
    ]
    rows = []
    for index, entry in enumerate(entries, start=1):
        severity = str(entry.get("severity", "?")).lower()
        seen = occurrences(entry, now)
        said = reported(entry, now)
        promotions = [p for p in (entry.get("promotions") or []) if isinstance(p, dict)]
        # Three facts stacked in one cell, coloured apart so the eye can pick
        # out the one it came for. Location goes under the title rather than
        # into a column of its own because it is a `path:line` routinely longer
        # than the title, and a column would either let it dominate the table
        # or truncate it past the point of being usable for the one thing it is
        # for. It is clipped to the first of several locations and to a line's
        # worth, because the ones that run to 400 characters are prose about
        # the location rather than a `path:line`; `--detail` has all of it.
        parts = [(str(entry.get("title") or "(untitled)"), None)]
        para_urls: Dict[int, str] = {}
        location = str(entry.get("location") or "")
        if location:
            parts.append((clip(location.split(" and ")[0], 110), "cyan"))
            # The paragraph is linked as a whole, to the first file it names --
            # the same reference the clip keeps. `--detail` links each of them
            # separately, which is the only place a location naming three files
            # has the room to offer three links.
            links = location_links(entry, repo, roots)
            if links:
                para_urls[len(parts) - 1] = links[0][1]
        verdict = verdicts.get(str(entry.get("fingerprint", "")), "")
        if verdict:
            parts.append((verdict, verdict_style(verdict)))
        rows.append(
            [
                (str(index), "dim"),
                (severity.upper(), SEVERITY_STYLE.get(severity, "dim")),
                (str(entry.get("signal", "?")), None),
                (str(entry.get("confidence") or "unstated"), "dim"),
                ("?" if seen is None else "%dx" % seen, None),
                ("?" if said is None else compact_count(said), "dim"),
                (str(len(promotions)) if promotions else "-", "green" if promotions else "dim"),
                (ago(parse_iso(entry.get("last_seen")), now), "dim"),
                (
                    "\n".join(text for text, _ in parts),
                    None,
                    None,
                    {i: style for i, (_, style) in enumerate(parts) if style},
                    para_urls,
                ),
            ]
        )
    return render_table(columns, rows, palette, width, box), entries


def render_promotions(
    pairs: Sequence[Tuple[Dict[str, Any], Dict[str, Any]]],
    now: _dt.datetime,
    palette: Palette,
    width: int,
    box: Dict[str, str],
    utc: bool,
) -> List[str]:
    # The pull-request reference is the point of this table, so it and the
    # finding it answers are the last things to go; the severity is already in
    # the findings table above.
    columns = [
        Column("WHEN", expendable=1),
        Column("AGE", align="r"),
        Column("SEV", expendable=2),
        Column("PULL REQUEST"),
        Column("FINDING", wrap=True, min_width=24),
    ]
    rows = []
    for promotion, entry in pairs:
        at = parse_iso(promotion.get("at"))
        severity = str(entry.get("severity", "?")).lower()
        url = str(promotion.get("url") or "")
        if url:
            label, style = pr_ref(url), "blue"
        else:
            label, style = "(filed, no URL recorded)", "yellow"
        if promotion.get("unconfirmed"):
            label += " [unconfirmed]"
            style = "yellow"
        rows.append(
            [
                (stamp(at, utc), None),
                (ago(at, now), "dim"),
                (severity.upper(), SEVERITY_STYLE.get(severity, "dim")),
                (label, style, url),
                (str(entry.get("title") or "(untitled)"), "dim"),
            ]
        )
    return render_table(columns, rows, palette, width, box)


def render_detail(
    entry: Dict[str, Any],
    verdict: str,
    now: _dt.datetime,
    palette: Palette,
    width: int,
    utc: bool,
    repo: str = "",
    roots: frozenset = frozenset(),
) -> List[str]:
    severity = str(entry.get("severity", "?")).lower()
    wrap = max(40, width - 4)

    def block(label: str, text: str, style: Optional[str] = None) -> List[str]:
        if not text:
            return []
        lines = [palette("  " + label, "dim")]
        for para in str(text).split("\n"):
            lines.extend("    " + palette(line, style) for line in (textwrap.wrap(para, wrap - 4) or [""]))
        return lines + [""]

    head = "%s  %s  %s" % (
        palette(severity.upper(), SEVERITY_STYLE.get(severity, "dim")),
        palette(str(entry.get("signal", "?")), "bold"),
        palette(str(entry.get("fingerprint", "?")), "dim"),
    )
    out = [head, ""]
    out.extend(block("title", str(entry.get("title") or "(untitled)"), "bold"))
    out.extend(block("location", str(entry.get("location") or "(not localised)"), "cyan"))

    # The location as written is prose and stays that way; these are the file
    # references pulled out of it, one per line so each is short enough to
    # survive as a single clickable link, pinned to the revision the finding was
    # made against. A location that names files in another repository, or names
    # none at all, produces no block rather than a dead link.
    links = location_links(entry, repo, roots)
    if links:
        out.append(palette("  open", "dim"))
        out.extend("    " + hyperlink(palette(label, "blue"), url, palette) for label, url in links)
        out.append("")

    seen = occurrences(entry, now)
    said = reported(entry, now)
    out.extend(
        block(
            "counts",
            "seen %s in the last 24h (runs) · reported %s occurrence(s) · confidence %s"
            % (
                "?" if seen is None else "%dx" % seen,
                "?" if said is None else compact_count(said),
                entry.get("confidence") or "unstated",
            ),
        )
    )
    out.extend(
        block(
            "timeline",
            "first seen %s\nlast seen  %s\nrevision   %s"
            % (
                stamp(parse_iso(entry.get("first_seen")), utc),
                stamp(parse_iso(entry.get("last_seen")), utc),
                str(entry.get("revision") or "-"),
            ),
        )
    )
    if verdict:
        out.extend(block("gate", verdict, verdict_style(verdict)))
    out.extend(block("summary", str(entry.get("summary") or "")))
    out.extend(block("user impact", str(entry.get("user_impact") or "")))
    out.extend(block("evidence", str(entry.get("evidence") or ""), "dim"))
    out.extend(block("proposed fix", str(entry.get("proposed_fix") or "")))

    promotions = [p for p in (entry.get("promotions") or []) if isinstance(p, dict)]
    if promotions:
        out.extend(
            block(
                "pull requests",
                "\n".join(
                    "%s  %s%s"
                    % (
                        stamp(parse_iso(p.get("at")), utc),
                        p.get("url") or "(no URL recorded)",
                        "  [unconfirmed]" if p.get("unconfirmed") else "",
                    )
                    for p in promotions
                ),
                "green",
            )
        )

    refusal = entry.get("refused")
    if isinstance(refusal, dict):
        out.extend(
            block(
                "refused",
                "%s\nat %s (%s)"
                % (
                    refusal.get("reason") or "no reason recorded",
                    stamp(parse_iso(refusal.get("at")), utc),
                    short_rev(refusal.get("revision")),
                ),
                "magenta",
            )
        )
    return out


def match_finding(entries: List[Dict[str, Any]], needle: str) -> Optional[Dict[str, Any]]:
    """Accepts a table row number or a fingerprint prefix, in that order.

    Row number first because it is what the reader has just been shown, and a
    16-hex-character fingerprint is never a bare integer, so the two cannot
    collide.
    """
    if needle.isdigit():
        index = int(needle)
        if 1 <= index <= len(entries):
            return entries[index - 1]
    lowered = needle.lower()
    hits = [e for e in entries if str(e.get("fingerprint", "")).lower().startswith(lowered)]
    return hits[0] if len(hits) == 1 else None


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    return _add_arguments(
        argparse.ArgumentParser(
            prog="selfimprove_ledger_view.py",
            description="Render the self-improvement ledger ConfigMap as a readable report.",
            epilog=(
                "examples:\n"
                "  scripts/selfimprove_ledger_view.py\n"
                "  scripts/selfimprove_ledger_view.py --severity medium --sort seen\n"
                "  scripts/selfimprove_ledger_view.py --detail 3\n"
                "  scripts/selfimprove_ledger_view.py --json | jq '.findings'\n"
                "  kubectl -n kubeagents-system get cm kube-agents-selfimprove-ledger -o json > l.json\n"
                "  scripts/selfimprove_ledger_view.py --file l.json\n"
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
    )


def _add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "-n", "--namespace", default=os.environ.get("SELFIMPROVE_NAMESPACE", DEFAULT_NAMESPACE)
    )
    parser.add_argument(
        "-c",
        "--configmap",
        default=os.environ.get("SELFIMPROVE_LEDGER_CONFIGMAP", DEFAULT_CONFIGMAP),
    )
    parser.add_argument("--cronjob", default=DEFAULT_CRONJOB, help="CronJob to read the mode and gate from")
    parser.add_argument(
        "--no-cronjob", action="store_true", help="skip the CronJob read (no mode, schedule or gate)"
    )
    parser.add_argument("--context", default=None, help="kubectl context; defaults to the current one")
    parser.add_argument(
        "-f", "--file", default=None, help="read a ledger or ConfigMap from a file, or - for stdin"
    )
    parser.add_argument("--detail", default=None, metavar="N|FINGERPRINT", help="full record for one finding")
    parser.add_argument(
        "--runs", type=int, default=10, help="runs to show, newest first; 0 for all (default 10)"
    )
    parser.add_argument(
        "--severity", choices=SEVERITY_ORDER, default=None, help="hide findings below this severity"
    )
    parser.add_argument("--signal", default=None, help="only findings in this signal class")
    parser.add_argument("--sort", choices=("severity", "seen", "last", "first"), default="severity")
    parser.add_argument("--json", action="store_true", help="print the raw ledger JSON and exit")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    parser.add_argument(
        "--ascii", action="store_true", help="ASCII borders instead of box-drawing characters"
    )
    parser.add_argument("--utc", action="store_true", help="timestamps in UTC instead of local time")
    parser.add_argument("--width", type=int, default=0, help="output width; 0 detects the terminal")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.file:
            ledger, raw = load_from_file(args.file)
            source = "file"
            cronjob = None
        else:
            ledger, raw = load_from_cluster(args.namespace, args.configmap, args.context)
            source = current_context(args.context)
            cronjob = None if args.no_cronjob else load_cronjob(args.namespace, args.cronjob, args.context)
    except LoadError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print("error: could not read the ledger: %s" % exc, file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(ledger, indent=2, sort_keys=True))
        return 0

    if not isinstance(ledger, dict) or "findings" not in ledger:
        print("error: that does not look like a ledger (no `findings` key)", file=sys.stderr)
        return 1

    palette = Palette(want_colour(args.color))
    box = BOX_ASCII if args.ascii else BOX_UNICODE
    width = args.width or shutil.get_terminal_size((120, 40)).columns
    width = max(60, min(width, 200))
    now = _dt.datetime.now(_dt.timezone.utc)

    env = cronjob_env(cronjob)
    gate = parse_gate(env)
    verdicts = gate_verdicts(ledger, gate, now)
    repo, roots = target_repo(env), repo_toplevel()

    if args.detail:
        entries = sorted_findings(ledger)
        entries.sort(key=lambda e: (severity_rank(e), -(occurrences(e, now) or 0)))
        entry = match_finding(entries, args.detail)
        if entry is None:
            print(
                "error: no finding matches %r (try a row number or a fingerprint prefix)" % args.detail,
                file=sys.stderr,
            )
            return 1
        for line in render_detail(
            entry,
            verdicts.get(str(entry.get("fingerprint", "")), ""),
            now,
            palette,
            width,
            args.utc,
            repo,
            roots,
        ):
            print(line)
        return 0

    out: List[str] = []
    out.extend(
        render_header(ledger, raw, source, args.namespace, args.configmap, cronjob, env, gate, now, palette, args.utc)
    )
    out.append("")
    out.append(palette("RUNS", "head"))
    out.extend(render_runs(ledger, args.runs, now, palette, width, box, args.utc))
    out.append("")
    out.append(palette("FINDINGS", "head"))
    table, entries = render_findings(
        ledger, verdicts, now, palette, width, box, args.sort, args.severity, args.signal, repo, roots
    )
    out.extend(table)

    # Ledger-wide rather than filtered: "what has this loop actually opened" is
    # a fact about the install, and a --severity filter narrowing it would hide
    # pull requests still open against the findings it hid.
    promotions = collect_promotions(sorted_findings(ledger))
    out.append("")
    out.append(palette("PULL REQUESTS OPENED", "head"))
    if promotions:
        out.extend(render_promotions(promotions, now, palette, width, box, args.utc))
    else:
        out.append(
            palette(
                "  none recorded. Under report-only the loop promotes and deliberately does not file;"
                " in fork or upstream mode an empty list under a non-zero promoted count means the"
                " GitHub path failed or the finding was refused.",
                "dim",
            )
        )

    out.append("")
    if verdicts:
        out.append(
            palette(
                "  gate lines simulate the next run re-finding everything, against the CronJob's current gate",
                "dim",
            )
        )
    out.append(palette("  --detail <#> for one finding in full · --help for filters", "dim"))

    for line in out:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
