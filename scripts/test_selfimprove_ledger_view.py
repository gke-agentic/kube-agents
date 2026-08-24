#!/usr/bin/env python3
"""Unit tests for the self-improvement ledger viewer.

Run: cd scripts && python3 -m unittest test_selfimprove_ledger_view

Every test here is cluster-free: the viewer's `--file` path takes a ledger
somebody has already pulled down, which is the same door the renderers come in
through. Nothing below shells out to kubectl.

Two classes of failure are worth more than the rest. The first is a misaligned
table, which is the whole reason this tool exists over `kubectl | jq` -- and it
breaks silently, because a colour code or a hyperlink occupies bytes and no
columns, so the width arithmetic is right up until it is measuring an escape
sequence. Several tests below assert that every rendered line of a table has
the same *visible* width, which is the only statement of correctness that
catches that. The second is the viewer quietly disagreeing with the loop: the
occurrence counts and the gate verdicts are the loop's own functions, reused,
and `TestReusesTheLoopsOwnMaths` is what says so.
"""

import contextlib
import datetime as _dt
import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import selfimprove_ledger_view as view

NOW = _dt.datetime(2026, 8, 23, 20, 0, 0, tzinfo=_dt.timezone.utc)


def iso(hours_ago):
    return (NOW - _dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def finding(fingerprint, severity, title, **overrides):
    entry = {
        "fingerprint": fingerprint,
        "signal": "errors",
        "severity": severity,
        "title": title,
        "location": "k8s-operator/internal/controller/platformagent_controller.go:1090",
        "summary": "a summary",
        "evidence": "some evidence",
        "proposed_fix": "a fix",
        "confidence": "high",
        "user_impact": "an impact",
        "first_seen": iso(6),
        "last_seen": iso(1),
        "revision": "aa3b7aa1111111111111111111111111111111",
        "sightings": [{"at": iso(3), "count": 4}, {"at": iso(1), "count": 5}],
        "promotions": [],
    }
    entry.update(overrides)
    return entry


def ledger(**overrides):
    document = {
        "version": 1,
        "findings": {
            "aaaa000000000000": finding("aaaa000000000000", "medium", "A medium finding"),
            "bbbb111111111111": finding(
                "bbbb111111111111",
                "critical",
                "A critical finding",
                signal="latency",
                promotions=[
                    {
                        "at": iso(2),
                        "url": "https://github.com/gke-agentic/kube-agents/pull/160",
                        "revision": "aa3b7aa",
                    }
                ],
            ),
            "cccc222222222222": finding(
                "cccc222222222222", "low", "A low finding", signal="inefficiency"
            ),
        },
        "runs": [
            {"at": iso(3), "revision": "62cdf89", "outcome": "ok", "findings": 4, "promoted": 2, "filed": 1, "note": ""},
            {"at": iso(1), "revision": "aa3b7aa", "outcome": "ok", "findings": 5, "promoted": 1, "filed": 0, "note": ""},
        ],
    }
    document.update(overrides)
    return document


GATE = {
    "maxPullRequestsPerDay": 3,
    "cooldownHours": 24,
    "rules": [
        {"severity": "critical", "minOccurrencesPerDay": 1},
        {"severity": "medium", "minOccurrencesPerDay": 2},
    ],
}


@contextlib.contextmanager
def ledger_file(document):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(document, handle)
        path = handle.name
    try:
        yield path
    finally:
        os.unlink(path)


def run_main(argv):
    """`main` with stdout captured, returning (exit code, text)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        code = view.main(argv)
    return code, buffer.getvalue()


def table_blocks(text):
    """The report's tables, as runs of consecutive bordered lines.

    Grouped rather than lumped together because a report holds several tables
    with different columns, and only lines from the same table have any reason
    to agree on a width.
    """
    blocks, current = [], []
    for line in text.splitlines():
        if line and line[0] in "┌│├└+|":
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


# --------------------------------------------------------------------------


class TestWidthMeasurement(unittest.TestCase):
    """`plain` is the only thing standing between colour and a broken table."""

    def test_strips_sgr_colour(self):
        self.assertEqual(view.plain("\033[1;31mred\033[0m"), "red")

    def test_strips_osc_8_hyperlinks(self):
        linked = "\x1b]8;;https://example.com/a/very/long/url\x1b\\text\x1b]8;;\x1b\\"
        self.assertEqual(view.plain(linked), "text")

    def test_a_hyperlinked_cell_measures_as_its_text_not_its_url(self):
        palette = view.Palette(True)
        cell = view.hyperlink("short", "https://github.com/o/r/pull/12345", palette)
        self.assertGreater(len(cell), 30)
        self.assertEqual(len(view.plain(cell)), len("short"))

    def test_padding_uses_visible_width(self):
        palette = view.Palette(True)
        padded = view._pad(palette("ab", "red"), 6, "l")
        self.assertEqual(len(view.plain(padded)), 6)


class TestTableRendering(unittest.TestCase):
    def render(self, columns, rows, width=100, colour=True, box=None):
        return view.render_table(
            columns, rows, view.Palette(colour), width, box or view.BOX_UNICODE
        )

    def test_every_line_has_the_same_visible_width(self):
        columns = [view.Column("A"), view.Column("B", wrap=True, min_width=10)]
        rows = [
            [("x", "red"), ("a much longer cell that will certainly need wrapping", "dim")],
            [("yy", None), ("short", None)],
        ]
        widths = {len(view.plain(line)) for line in self.render(columns, rows, width=60)}
        self.assertEqual(len(widths), 1, "table lines disagree on width: %s" % sorted(widths))

    def test_colour_does_not_change_the_layout(self):
        columns = [view.Column("A"), view.Column("B", wrap=True)]
        rows = [[("x", "red"), ("some text here", "green")]]
        coloured = [view.plain(line) for line in self.render(columns, rows)]
        self.assertEqual(coloured, self.render(columns, rows, colour=False))

    def test_a_hyperlink_does_not_change_the_layout(self):
        columns = [view.Column("PR"), view.Column("T", wrap=True, min_width=20)]
        url = "https://github.com/gke-agentic/kube-agents/pull/160"
        with_link = self.render(columns, [[("o/r#160", "blue", url), ("a title", None)]])
        without = self.render(columns, [[("o/r#160", "blue"), ("a title", None)]])
        self.assertEqual([view.plain(l) for l in with_link], [view.plain(l) for l in without])

    def test_a_link_is_emitted_once_and_never_on_a_blank_continuation_line(self):
        """Regression: the OSC sequence was wrapped around the empty padding
        lines a taller neighbouring column produces, which every terminal
        renders as visible escape litter and no reader can click."""
        columns = [view.Column("PR"), view.Column("T", wrap=True, min_width=12)]
        url = "https://github.com/gke-agentic/kube-agents/pull/160"
        rows = [[("o/r#160", "blue", url), ("a title long enough to wrap over several lines", None)]]
        lines = self.render(columns, rows, width=44)
        self.assertGreater(sum(1 for line in lines if "o/r#160" in view.plain(line)), 0)
        self.assertEqual(sum(line.count("\x1b]8;;" + url) for line in lines), 1)

    def test_per_line_styles_colour_each_paragraph_of_one_cell(self):
        columns = [view.Column("C", wrap=True, min_width=30)]
        rows = [[("title\nlocation\nverdict", None, None, {1: "cyan", 2: "green"})]]
        lines = self.render(columns, rows, width=40)
        body = [l for l in lines if "location" in view.plain(l) or "verdict" in view.plain(l)]
        self.assertTrue(any(view.STYLES["cyan"] in l for l in body))
        self.assertTrue(any(view.STYLES["green"] in l for l in body))
        title = [l for l in lines if "title" in view.plain(l)][0]
        self.assertNotIn(view.STYLES["cyan"], title)

    def test_a_per_paragraph_link_reaches_a_cell_no_whole_cell_link_could(self):
        """The FINDING cell always stacks a title over a location, so it never
        has the single-line form a whole-cell URL requires. The per-paragraph
        map is the only way its location is ever clickable."""
        columns = [view.Column("C", wrap=True, min_width=40)]
        url = "https://github.com/o/r/blob/abc/x.go#L1"
        cell = ("a title\nx.go:1", None, None, {1: "cyan"}, {1: url})
        lines = self.render(columns, [[cell]], width=50)
        self.assertEqual(sum(line.count("\x1b]8;;" + url) for line in lines), 1)
        # The whole-cell form on the same cell renders no link at all.
        plain_cell = ("a title\nx.go:1", None, url)
        self.assertEqual(
            sum(l.count("\x1b]8;;" + url) for l in self.render(columns, [[plain_cell]], width=50)),
            0,
        )

    def test_a_per_paragraph_link_is_dropped_when_that_paragraph_wraps(self):
        """A link split across two table rows renders as two links, so a
        paragraph too long for the column loses its link rather than being
        drawn broken. The text still shows."""
        columns = [view.Column("C", wrap=True, min_width=12)]
        url = "https://github.com/o/r/blob/abc/x.go#L1"
        cell = ("t\nsome/quite/long/path/that/will/wrap.go:1", None, None, None, {1: url})
        lines = self.render(columns, [[cell]], width=24)
        self.assertEqual(sum(line.count("\x1b]8;;") for line in lines), 0)
        self.assertIn("wrap.go", "".join(view.plain(line) for line in lines))

    def test_a_per_paragraph_link_does_not_change_the_layout(self):
        columns = [view.Column("C", wrap=True, min_width=40)]
        url = "https://github.com/o/r/blob/abc/x.go#L1"
        linked = self.render(columns, [[("a title\nx.go:1", None, None, None, {1: url})]])
        bare = self.render(columns, [[("a title\nx.go:1", None)]])
        self.assertEqual([view.plain(l) for l in linked], [view.plain(l) for l in bare])

    def test_long_unbroken_text_is_broken_rather_than_overflowing(self):
        columns = [view.Column("P", wrap=True, min_width=10)]
        rows = [[("a/very/long/path/with/no/spaces/at/all/in/it/anywhere.go:1090", None)]]
        widths = {len(view.plain(line)) for line in self.render(columns, rows, width=30)}
        self.assertEqual(len(widths), 1)

    def test_ascii_mode_emits_no_box_drawing_characters(self):
        columns = [view.Column("A"), view.Column("B")]
        lines = self.render(columns, [[("x", None), ("y", None)]], box=view.BOX_ASCII)
        self.assertFalse(any(ch in "".join(lines) for ch in "─│┌┬┐├┼┤└┴┘"))

    def test_a_narrow_terminal_keeps_the_minimum_rather_than_collapsing(self):
        columns = [view.Column("LONG COLUMN NAME"), view.Column("B", wrap=True, min_width=14)]
        rows = [[("a value", None), ("some wrapping text goes here", None)]]
        lines = self.render(columns, rows, width=20)
        self.assertEqual(len({len(view.plain(l)) for l in lines}), 1)
        self.assertIn("some", "".join(view.plain(l) for l in lines))


class TestColourSelection(unittest.TestCase):
    def test_explicit_flags_win(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False):
            self.assertTrue(view.want_colour("always"))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(view.want_colour("never"))

    def test_no_color_beats_a_tty(self):
        tty = mock.Mock()
        tty.isatty.return_value = True
        with mock.patch.dict(os.environ, {"NO_COLOR": ""}, clear=True):
            self.assertFalse(view.want_colour("auto", tty))

    def test_dumb_terminal_is_not_coloured(self):
        tty = mock.Mock()
        tty.isatty.return_value = True
        with mock.patch.dict(os.environ, {"TERM": "dumb"}, clear=True):
            self.assertFalse(view.want_colour("auto", tty))

    def test_auto_follows_the_tty(self):
        for isatty, expected in ((True, True), (False, False)):
            stream = mock.Mock()
            stream.isatty.return_value = isatty
            with mock.patch.dict(os.environ, {"TERM": "xterm"}, clear=True):
                self.assertIs(view.want_colour("auto", stream), expected)

    def test_a_disabled_palette_emits_no_escapes(self):
        palette = view.Palette(False)
        self.assertEqual(palette("text", "red"), "text")
        self.assertEqual(view.hyperlink("text", "https://example.com", palette), "text")


class TestFormatting(unittest.TestCase):
    def test_pr_ref_shortens_a_github_pull_request(self):
        self.assertEqual(
            view.pr_ref("https://github.com/gke-agentic/kube-agents/pull/160"),
            "gke-agentic/kube-agents#160",
        )

    def test_pr_ref_tolerates_a_trailing_slash(self):
        self.assertEqual(view.pr_ref("https://github.com/o/r/pull/1/"), "o/r#1")

    def test_pr_ref_leaves_anything_else_alone(self):
        for other in ("https://github.com/o/r/issues/5", "not a url", ""):
            self.assertEqual(view.pr_ref(other), other)

    def test_parse_iso_accepts_the_ledgers_own_format(self):
        self.assertEqual(view.parse_iso("2026-08-23T17:07:51Z").year, 2026)

    def test_parse_iso_assumes_utc_for_a_naive_stamp(self):
        self.assertEqual(view.parse_iso("2026-08-23T17:07:51").tzinfo, _dt.timezone.utc)

    def test_parse_iso_returns_none_rather_than_raising(self):
        for junk in ("", "   ", "yesterday", None, 17, {}):
            self.assertIsNone(view.parse_iso(junk))

    def test_humanise_delta_scales(self):
        self.assertEqual(view.humanise_delta(45), "45s")
        self.assertEqual(view.humanise_delta(600), "10m")
        self.assertEqual(view.humanise_delta(3600), "1h")
        self.assertEqual(view.humanise_delta(3600 + 120), "1h02m")
        self.assertEqual(view.humanise_delta(86400 * 2), "2d")
        self.assertEqual(view.humanise_delta(86400 * 2 + 3600 * 5), "2d5h")

    def test_ago_reads_forwards_and_backwards(self):
        self.assertEqual(view.ago(NOW - _dt.timedelta(hours=2), NOW), "2h ago")
        self.assertEqual(view.ago(NOW + _dt.timedelta(minutes=30), NOW), "in 30m")
        self.assertEqual(view.ago(None, NOW), "never")

    def test_stamp_in_utc_is_stable_regardless_of_the_readers_zone(self):
        self.assertEqual(view.stamp(view.parse_iso("2026-08-23T17:07:51Z"), True), "2026-08-23 17:07 UTC")
        self.assertEqual(view.stamp(None, True), "-")

    def test_stamp_local_is_lower_case_am_pm(self):
        rendered = view.stamp(view.parse_iso("2026-08-23T17:07:51Z"), False)
        self.assertNotIn("AM", rendered)
        self.assertNotIn("PM", rendered)
        self.assertTrue(rendered.startswith("2026-08-2"))

    def test_compact_count_shortens_the_big_ones(self):
        self.assertEqual(view.compact_count(9), "9")
        self.assertEqual(view.compact_count(999), "999")
        self.assertEqual(view.compact_count(6400), "6.4k")
        self.assertEqual(view.compact_count(2_500_000), "2.5M")

    def test_clip_only_shortens_what_is_too_long(self):
        self.assertEqual(view.clip("short", 20), "short")
        clipped = view.clip("x" * 50, 10)
        self.assertEqual(len(clipped), 10)
        self.assertTrue(clipped.endswith("…"))

    def test_meter_is_always_the_requested_width(self):
        for fraction in (-1.0, 0.0, 0.33, 1.0, 4.2):
            self.assertEqual(len(view.meter(fraction, 18)), 18)

    def test_short_rev_handles_a_missing_revision(self):
        self.assertEqual(view.short_rev("aa3b7aa1111111"), "aa3b7aa")
        self.assertEqual(view.short_rev(None), "-")
        self.assertEqual(view.short_rev(""), "-")


class TestLoading(unittest.TestCase):
    def test_reads_a_bare_ledger(self):
        with ledger_file(ledger()) as path:
            document, raw = view.load_from_file(path)
        self.assertEqual(len(document["findings"]), 3)
        self.assertIn("findings", raw)

    def test_reads_a_whole_configmap(self):
        """`kubectl get cm -o json > x.json` is the shorter command, so it is
        the likelier thing to find in a file."""
        wrapped = {
            "kind": "ConfigMap",
            "metadata": {"name": view.DEFAULT_CONFIGMAP},
            "data": {view.LEDGER_KEY: json.dumps(ledger())},
        }
        with ledger_file(wrapped) as path:
            document, raw = view.load_from_file(path)
        self.assertEqual(len(document["findings"]), 3)
        # The raw text is the inner ledger, not the envelope: the size meter
        # measures the ledger against LEDGER_MAX_BYTES, and measuring the
        # ConfigMap's own JSON would overstate it.
        self.assertNotIn("ConfigMap", raw)

    def test_a_configmap_without_the_ledger_key_is_not_mistaken_for_one(self):
        with ledger_file({"data": {"other.json": "{}"}}) as path:
            document, _ = view.load_from_file(path)
        self.assertNotIn("findings", document)

    def test_cronjob_env_flattens_the_container_spec(self):
        cronjob = {
            "spec": {
                "jobTemplate": {
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {
                                        "env": [
                                            {"name": "SELFIMPROVE_MODE", "value": "fork"},
                                            {"name": "FROM_SECRET", "valueFrom": {}},
                                        ]
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        }
        env = view.cronjob_env(cronjob)
        self.assertEqual(env["SELFIMPROVE_MODE"], "fork")
        self.assertNotIn("FROM_SECRET", env)

    def test_cronjob_env_tolerates_a_shape_it_does_not_recognise(self):
        for junk in (None, {}, {"spec": {}}, {"spec": {"jobTemplate": "nonsense"}}):
            self.assertEqual(view.cronjob_env(junk), {})

    def test_parse_gate_tolerates_missing_and_malformed_json(self):
        self.assertEqual(view.parse_gate({}), {})
        self.assertEqual(view.parse_gate({"SELFIMPROVE_GATE": "not json"}), {})
        self.assertEqual(view.parse_gate({"SELFIMPROVE_GATE": "[1,2]"}), {})
        self.assertEqual(view.parse_gate({"SELFIMPROVE_GATE": json.dumps(GATE)}), GATE)

    def test_kubectl_missing_is_an_error_that_names_the_way_out(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
            with self.assertRaises(view.LoadError) as caught:
                view.kubectl_json(["get", "cm"], None)
        self.assertIn("--file", str(caught.exception))

    def test_a_configmap_without_the_key_says_the_loop_has_not_run(self):
        with mock.patch.object(view, "kubectl_json", return_value={"data": {}}):
            with self.assertRaises(view.LoadError) as caught:
                view.load_from_cluster("ns", "cm", None)
        self.assertIn("has not completed a run", str(caught.exception))

    def test_a_missing_cronjob_is_not_an_error(self):
        with mock.patch.object(view, "kubectl_json", side_effect=view.LoadError("nope")):
            self.assertIsNone(view.load_cronjob("ns", "cj", None))


class TestReusesTheLoopsOwnMaths(unittest.TestCase):
    """The counts and the gate come from `selfimprove_ledger`, not from here.

    Reimplementing either would give the viewer a second opinion about the same
    ledger, and the two would drift the first time either changed.
    """

    def setUp(self):
        if view.ledger_mod is None:
            self.skipTest("selfimprove_ledger is not importable from this checkout")

    def test_occurrences_counts_runs_and_reported_counts_claims(self):
        entry = finding("dddd", "medium", "t")
        self.assertEqual(view.occurrences(entry, NOW), 2)
        self.assertEqual(view.reported(entry, NOW), 9)

    def test_a_sighting_outside_the_window_stops_counting(self):
        entry = finding("dddd", "medium", "t", sightings=[{"at": iso(48), "count": 3}])
        self.assertEqual(view.occurrences(entry, NOW), 0)

    def test_gate_verdicts_cover_every_finding(self):
        verdicts = view.gate_verdicts(ledger(), GATE, NOW)
        self.assertEqual(set(verdicts), set(ledger()["findings"]))

    def test_the_cooldown_holds_a_recently_promoted_finding(self):
        verdicts = view.gate_verdicts(ledger(), GATE, NOW)
        self.assertIn("cooldown", verdicts["bbbb111111111111"])

    def test_a_severity_with_no_rule_is_held_and_says_so(self):
        verdicts = view.gate_verdicts(ledger(), GATE, NOW)
        self.assertIn("no promotion rule", verdicts["cccc222222222222"])

    def test_a_finding_that_clears_its_rule_is_promoted(self):
        verdicts = view.gate_verdicts(ledger(), GATE, NOW)
        self.assertTrue(verdicts["aaaa000000000000"].startswith("promoted"))

    def test_no_gate_means_no_verdicts_rather_than_a_guess(self):
        self.assertEqual(view.gate_verdicts(ledger(), {}, NOW), {})

    def test_a_malformed_gate_does_not_take_the_report_down(self):
        self.assertEqual(view.gate_verdicts(ledger(), {"rules": "not a list"}, NOW), {})

    def test_verdict_styles_separate_the_three_outcomes(self):
        self.assertEqual(view.verdict_style("promoted: medium at 2 occurrence(s)"), "green")
        self.assertEqual(view.verdict_style("held: the filing turn refused this permanently (x)"), "magenta")
        self.assertEqual(view.verdict_style("held: the day's budget is spent"), "yellow")

    def test_counts_degrade_to_none_when_the_module_is_absent(self):
        with mock.patch.object(view, "ledger_mod", None):
            self.assertIsNone(view.occurrences(finding("d", "low", "t"), NOW))
            self.assertIsNone(view.reported(finding("d", "low", "t"), NOW))
            self.assertEqual(view.gate_verdicts(ledger(), GATE, NOW), {})


class TestFindingSelection(unittest.TestCase):
    def render(self, document=None, sort="severity", severity=None, signal=None):
        document = document if document is not None else ledger()
        return view.render_findings(
            document, {}, NOW, view.Palette(False), 160, view.BOX_UNICODE, sort, severity, signal
        )

    def test_worst_first_by_default(self):
        _, entries = self.render()
        self.assertEqual([e["severity"] for e in entries], ["critical", "medium", "low"])

    def test_sorting_by_last_seen_puts_the_freshest_first(self):
        document = ledger()
        document["findings"]["cccc222222222222"]["last_seen"] = iso(0)
        document["findings"]["aaaa000000000000"]["last_seen"] = iso(9)
        _, entries = self.render(document, sort="last")
        self.assertEqual(entries[0]["fingerprint"], "cccc222222222222")

    def test_sorting_by_first_seen_puts_the_oldest_first(self):
        document = ledger()
        document["findings"]["cccc222222222222"]["first_seen"] = iso(99)
        _, entries = self.render(document, sort="first")
        self.assertEqual(entries[0]["fingerprint"], "cccc222222222222")

    def test_a_severity_floor_hides_everything_below_it(self):
        _, entries = self.render(severity="medium")
        self.assertEqual([e["severity"] for e in entries], ["critical", "medium"])

    def test_a_signal_filter_is_case_insensitive(self):
        _, entries = self.render(signal="LATENCY")
        self.assertEqual([e["fingerprint"] for e in entries], ["bbbb111111111111"])

    def test_a_filter_that_matches_nothing_says_so_rather_than_drawing_an_empty_table(self):
        lines, entries = self.render(signal="nonexistent")
        self.assertEqual(entries, [])
        self.assertIn("no findings match", "".join(lines))

    def test_an_unknown_severity_sorts_last_rather_than_raising(self):
        document = ledger()
        document["findings"]["aaaa000000000000"]["severity"] = "catastrophic"
        _, entries = self.render(document)
        self.assertEqual(entries[-1]["fingerprint"], "aaaa000000000000")

    def test_findings_may_be_a_list_as_well_as_a_dict(self):
        document = ledger(findings=list(ledger()["findings"].values()))
        self.assertEqual(len(view.sorted_findings(document)), 3)

    def test_match_finding_takes_a_row_number(self):
        _, entries = self.render()
        self.assertIs(view.match_finding(entries, "2"), entries[1])

    def test_match_finding_takes_a_fingerprint_prefix(self):
        _, entries = self.render()
        self.assertEqual(view.match_finding(entries, "cccc")["fingerprint"], "cccc222222222222")

    def test_match_finding_is_case_insensitive_on_the_fingerprint(self):
        _, entries = self.render()
        self.assertEqual(view.match_finding(entries, "CCCC2")["fingerprint"], "cccc222222222222")

    def test_match_finding_refuses_an_ambiguous_prefix(self):
        entries = [finding("ab11", "low", "one"), finding("ab22", "low", "two")]
        self.assertIsNone(view.match_finding(entries, "ab"))

    def test_match_finding_rejects_a_row_number_out_of_range(self):
        _, entries = self.render()
        self.assertIsNone(view.match_finding(entries, "0"))
        self.assertIsNone(view.match_finding(entries, "99"))


class TestPromotions(unittest.TestCase):
    def test_every_promotion_is_collected_newest_first(self):
        document = ledger()
        document["findings"]["aaaa000000000000"]["promotions"] = [
            {"at": iso(20), "url": "https://github.com/o/r/pull/1"},
            {"at": iso(0), "url": "https://github.com/o/r/pull/9"},
        ]
        pairs = view.collect_promotions(view.sorted_findings(document))
        self.assertEqual([p["url"].rsplit("/", 1)[-1] for p, _ in pairs], ["9", "160", "1"])

    def test_a_promotion_without_a_url_is_kept_and_labelled(self):
        """`record_promotion(confirmed=False)`: a filing turn that charged the
        budget without printing a link is precisely the row somebody has to go
        and look for by hand, so hiding it would hide the problem."""
        document = ledger()
        document["findings"]["aaaa000000000000"]["promotions"] = [{"at": iso(1), "unconfirmed": True}]
        pairs = view.collect_promotions(view.sorted_findings(document))
        lines = view.render_promotions(pairs, NOW, view.Palette(False), 160, view.BOX_UNICODE, True)
        text = "".join(lines)
        self.assertIn("no URL recorded", text)
        self.assertIn("unconfirmed", text)

    def test_the_promotion_table_stays_aligned(self):
        pairs = view.collect_promotions(view.sorted_findings(ledger()))
        lines = view.render_promotions(pairs, NOW, view.Palette(True), 120, view.BOX_UNICODE, True)
        self.assertEqual(len({len(view.plain(line)) for line in lines}), 1)


class TestHeader(unittest.TestCase):
    def header(self, document=None, **kwargs):
        document = document if document is not None else ledger()
        raw = json.dumps(document)
        defaults = dict(
            source="file",
            namespace="kubeagents-system",
            name=view.DEFAULT_CONFIGMAP,
            cronjob=None,
            env={},
            gate={},
            utc=True,
        )
        defaults.update(kwargs)
        return view.render_header(
            document,
            raw,
            defaults["source"],
            defaults["namespace"],
            defaults["name"],
            defaults["cronjob"],
            defaults["env"],
            defaults["gate"],
            NOW,
            view.Palette(False),
            defaults["utc"],
        )

    def test_the_first_line_leads_with_the_last_run_and_the_run_count(self):
        """The two questions anyone opens the ledger with, in the first place
        the eye lands. Asserted rather than left to drift because it is the
        one piece of the layout that was specified."""
        lead = self.header()[0]
        self.assertTrue(lead.startswith("last run"))
        self.assertIn("2026-08-23 19:00 UTC", lead)
        self.assertIn("1h ago", lead)
        self.assertIn("2 runs recorded", lead)

    def test_the_lead_line_reports_the_last_runs_outcome(self):
        document = ledger()
        document["runs"][-1]["outcome"] = "killed"
        self.assertIn("killed", self.header(document)[0])

    def test_a_ledger_with_no_runs_says_never_rather_than_a_dash(self):
        lead = self.header(ledger(runs=[]))[0]
        self.assertIn("never", lead)
        self.assertIn("0 runs recorded", lead)

    def test_one_run_is_singular(self):
        document = ledger()
        document["runs"] = document["runs"][:1]
        self.assertIn("1 run recorded", self.header(document)[0])

    def test_the_pull_request_count_is_all_time(self):
        self.assertIn("1 pull request(s) opened all time", "\n".join(self.header()))

    def test_a_file_source_does_not_claim_a_configmap(self):
        self.assertNotIn("configmap", "\n".join(self.header()))

    def test_a_cluster_source_names_the_configmap(self):
        text = "\n".join(self.header(source="gke_p_r_c"))
        self.assertIn("kubeagents-system/%s" % view.DEFAULT_CONFIGMAP, text)

    def test_report_only_mode_does_not_name_a_target_repository(self):
        text = "\n".join(
            self.header(env={"SELFIMPROVE_MODE": "report-only", "SELFIMPROVE_FORK_REPO": "o/r"})
        )
        self.assertIn("report-only", text)
        self.assertNotIn("o/r", text)

    def test_fork_mode_names_the_target_and_the_base(self):
        text = "\n".join(
            self.header(
                env={
                    "SELFIMPROVE_MODE": "fork",
                    "SELFIMPROVE_FORK_REPO": "gke-agentic/kube-agents",
                    "SELFIMPROVE_BASE_BRANCH": "self-improvement-live",
                }
            )
        )
        self.assertIn("gke-agentic/kube-agents", text)
        self.assertIn("self-improvement-live", text)

    def test_a_suspended_cronjob_is_called_out(self):
        cronjob = {"spec": {"schedule": "0 * * * *", "suspend": True}, "status": {}}
        text = "\n".join(self.header(cronjob=cronjob))
        self.assertIn("SUSPENDED", text)

    def test_an_active_cronjob_shows_its_schedule(self):
        cronjob = {
            "spec": {"schedule": "0 * * * *"},
            "status": {"lastScheduleTime": iso(0.05)},
        }
        text = "\n".join(self.header(cronjob=cronjob))
        self.assertIn("0 * * * *", text)
        self.assertIn("active", text)

    def test_the_budget_line_counts_the_promotions_in_the_window(self):
        if view.ledger_mod is None:
            self.skipTest("selfimprove_ledger is not importable from this checkout")
        text = "\n".join(self.header(gate=GATE))
        self.assertIn("1 of 3 pull requests", text)
        self.assertIn("24h cooldown", text)

    def test_the_size_meter_measures_the_ledger_against_the_cap(self):
        text = "\n".join(self.header())
        self.assertIn("of 768 KiB", text)


class TestRuns(unittest.TestCase):
    def render(self, document=None, limit=10):
        document = document if document is not None else ledger()
        return view.render_runs(document, limit, NOW, view.Palette(True), 140, view.BOX_UNICODE, True)

    def test_newest_run_first(self):
        rows = [view.plain(line) for line in self.render()]
        body = [r for r in rows if "2026-08-23" in r]
        self.assertIn("19:00 UTC", body[0])
        self.assertIn("17:00 UTC", body[1])

    def test_the_table_stays_aligned(self):
        self.assertEqual(len({len(view.plain(line)) for line in self.render()}), 1)

    def test_an_empty_history_says_so_rather_than_drawing_a_table(self):
        self.assertIn("no runs recorded yet", "".join(self.render(ledger(runs=[]))))

    def test_a_limit_shows_the_newest_and_says_how_many_it_hid(self):
        document = ledger(runs=[dict(ledger()["runs"][0], at=iso(h)) for h in range(20)])
        lines = self.render(document, limit=3)
        self.assertIn("17 older run(s) not shown", "".join(lines))

    def test_zero_means_all(self):
        document = ledger(runs=[dict(ledger()["runs"][0], at=iso(h)) for h in range(20)])
        self.assertNotIn("older run(s)", "".join(self.render(document, limit=0)))

    def test_an_unknown_outcome_is_not_styled_as_a_success(self):
        document = ledger()
        document["runs"][-1]["outcome"] = "something-new"
        joined = "".join(self.render(document))
        self.assertIn(view.STYLES["yellow"] + "something-new", joined)


class TestDetail(unittest.TestCase):
    def detail(self, entry, verdict=""):
        return "\n".join(view.render_detail(entry, verdict, NOW, view.Palette(False), 100, True))

    def test_shows_the_fields_the_table_has_to_leave_out(self):
        text = self.detail(finding("aaaa", "high", "A title"))
        for expected in ("a summary", "some evidence", "a fix", "an impact", "aaaa"):
            self.assertIn(expected, text)

    def test_shows_the_full_location_the_table_clips(self):
        long_location = "some/very/long/path.go:10 and " + "x" * 300
        text = self.detail(finding("aaaa", "high", "t", location=long_location))
        self.assertIn("x" * 40, text)

    def test_shows_every_pull_request(self):
        entry = finding(
            "aaaa",
            "high",
            "t",
            promotions=[
                {"at": iso(5), "url": "https://github.com/o/r/pull/1"},
                {"at": iso(1), "url": "https://github.com/o/r/pull/2"},
            ],
        )
        text = self.detail(entry)
        self.assertIn("pull/1", text)
        self.assertIn("pull/2", text)

    def test_shows_a_permanent_refusal_and_its_reason(self):
        entry = finding(
            "aaaa", "high", "t", refused={"at": iso(2), "reason": "touches the gate", "revision": "abc1234def"}
        )
        text = self.detail(entry)
        self.assertIn("touches the gate", text)
        self.assertIn("abc1234", text)

    def test_omits_a_block_it_has_nothing_for(self):
        entry = finding("aaaa", "high", "t", evidence="", proposed_fix="")
        text = self.detail(entry)
        self.assertNotIn("evidence", text)
        self.assertNotIn("proposed fix", text)


#: Stands in for the repository's top-level entries. Real runs derive this from
#: the checkout the script ships in; pinning it here keeps the tests from
#: changing meaning the next time a top-level directory is added or removed.
ROOTS = frozenset({"agents", "k8s-operator", "charts", "docs", "scripts", "images.json"})
REPO = "gke-agentic/kube-agents"


class TestLocationParsing(unittest.TestCase):
    def refs(self, location, roots=ROOTS):
        return view.location_refs(location, roots)

    def test_a_bare_path_and_line(self):
        self.assertEqual(
            self.refs("k8s-operator/cmd/main.go:108"), [("k8s-operator/cmd/main.go", "108")]
        )

    def test_a_path_with_no_line(self):
        self.assertEqual(self.refs("images.json"), [("images.json", None)])

    def test_a_second_reference_given_as_a_bare_line_number(self):
        """The live ledger writes `...controller.go:1090 (...) and :1162 (...)`,
        so a bare line number attaches to the path before it."""
        location = "k8s-operator/internal/controller/platformagent_controller.go:1090 (a) and :1162 (b)"
        self.assertEqual(
            self.refs(location),
            [
                ("k8s-operator/internal/controller/platformagent_controller.go", "1090"),
                ("k8s-operator/internal/controller/platformagent_controller.go", "1162"),
            ],
        )

    def test_code_in_the_prose_does_not_detach_a_following_bare_line(self):
        """Regression: the backticked `r.Status().Update(...)` between the two
        references parses as a dotted path, and treating it as a foreign one
        cost `:1162` its link."""
        location = (
            "k8s-operator/internal/controller/platformagent_controller.go:1090 "
            "(`return newPhase, r.Status().Update(ctx, agent)`) and :1162 (the other)"
        )
        self.assertEqual(len(self.refs(location)), 2)

    def test_a_real_path_in_another_repository_does_detach_it(self):
        self.assertEqual(self.refs("agent/foo.py:10 and :20"), [])

    def test_a_line_range_is_kept_whole(self):
        self.assertEqual(
            self.refs("charts/kube-agents/templates/self-improvement.yaml:611-612"),
            [("charts/kube-agents/templates/self-improvement.yaml", "611-612")],
        )

    def test_another_repositorys_paths_are_not_linked(self):
        """`agent/anthropic_adapter.py` is the Hermes harness. A kube-agents
        blob URL for it 404s, which reads as a stale finding rather than a bad
        link."""
        self.assertEqual(self.refs("agent/anthropic_adapter.py:136 (_is_claude_model)"), [])

    def test_prose_that_only_looks_like_a_path_is_rejected(self):
        for text in ("e.g. the handler", "hermes v2026.8.13 took 1.5s", "see Note: 4"):
            self.assertEqual(self.refs(text), [], text)

    def test_a_url_in_the_prose_is_not_mined_for_paths(self):
        self.assertEqual(self.refs("see https://github.com/o/r/blob/main/x.py here"), [])

    def test_repeats_are_collapsed(self):
        self.assertEqual(len(self.refs("docs/a.md:1 and docs/a.md:1")), 1)

    def test_no_roots_means_no_references(self):
        self.assertEqual(self.refs("k8s-operator/cmd/main.go:108", frozenset()), [])


class TestBlobUrls(unittest.TestCase):
    def test_pins_the_revision_the_finding_was_made_against(self):
        self.assertEqual(
            view.blob_url(REPO, "abc123", "k8s-operator/cmd/main.go", "108"),
            "https://github.com/gke-agentic/kube-agents/blob/abc123/k8s-operator/cmd/main.go#L108",
        )

    def test_a_range_repeats_the_L_the_way_github_spells_it(self):
        self.assertTrue(view.blob_url(REPO, "abc", "x.py", "48-54").endswith("#L48-L54"))

    def test_no_line_means_no_anchor(self):
        self.assertTrue(view.blob_url(REPO, "abc", "x.py").endswith("/x.py"))

    def test_a_missing_ingredient_yields_no_url(self):
        self.assertEqual(view.blob_url("", "abc", "x.py", "1"), "")
        self.assertEqual(view.blob_url(REPO, "", "x.py", "1"), "")
        self.assertEqual(view.blob_url(REPO, "abc", "", "1"), "")

    def test_a_finding_with_no_revision_is_not_linked(self):
        entry = finding("aaaa", "high", "t", revision="")
        self.assertEqual(view.location_links(entry, REPO, ROOTS), [])


class TestRepoToplevel(unittest.TestCase):
    def test_derives_the_set_from_the_checkout_this_script_ships_in(self):
        roots = view.repo_toplevel()
        self.assertIn("k8s-operator", roots)
        self.assertIn("agents", roots)
        self.assertNotIn(".git", roots)
        self.assertNotIn("agent", roots)

    def test_a_directory_that_is_not_a_checkout_switches_linking_off(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(view.repo_toplevel(empty), frozenset())


class TestTargetRepo(unittest.TestCase):
    def test_fork_mode_resolves_against_the_fork(self):
        env = {
            "SELFIMPROVE_MODE": "fork",
            "SELFIMPROVE_FORK_REPO": "fork/repo",
            "SELFIMPROVE_UPSTREAM_REPO": "up/repo",
        }
        self.assertEqual(view.target_repo(env), "fork/repo")

    def test_report_only_resolves_against_upstream(self):
        """Nothing is pushed to a fork under report-only, so the revision is
        only findable upstream."""
        env = {
            "SELFIMPROVE_MODE": "report-only",
            "SELFIMPROVE_FORK_REPO": "fork/repo",
            "SELFIMPROVE_UPSTREAM_REPO": "up/repo",
        }
        self.assertEqual(view.target_repo(env), "up/repo")

    def test_no_configuration_yields_no_repo(self):
        self.assertEqual(view.target_repo({}), "")


class TestLocationLinksInTheReport(unittest.TestCase):
    def test_the_findings_table_links_the_location(self):
        table, _ = view.render_findings(
            ledger(), {}, NOW, view.Palette(True), 200, view.BOX_UNICODE,
            "severity", None, None, REPO, ROOTS,
        )
        text = "\n".join(table)
        self.assertIn(
            "\x1b]8;;https://github.com/gke-agentic/kube-agents/blob/"
            "aa3b7aa1111111111111111111111111111111/"
            "k8s-operator/internal/controller/platformagent_controller.go#L1090",
            text,
        )

    def test_no_repo_means_no_links_and_the_same_text(self):
        args = (ledger(), {}, NOW, view.Palette(True), 200, view.BOX_UNICODE, "severity", None, None)
        linked, _ = view.render_findings(*args, REPO, ROOTS)
        bare, _ = view.render_findings(*args)
        self.assertNotIn("\x1b]8;;", "\n".join(bare))
        self.assertEqual([view.plain(l) for l in linked], [view.plain(l) for l in bare])

    def test_detail_lists_every_reference_separately(self):
        entry = finding(
            "aaaa",
            "high",
            "t",
            location="k8s-operator/cmd/main.go:108 and agents/platform/scripts/x.py:4",
        )
        text = "\n".join(
            view.render_detail(entry, "", NOW, view.Palette(True), 100, True, REPO, ROOTS)
        )
        self.assertIn("open", view.plain(text))
        self.assertIn("main.go#L108", text)
        self.assertIn("x.py#L4", text)

    def test_detail_omits_the_block_when_nothing_is_linkable(self):
        entry = finding("aaaa", "high", "t", location="agent/anthropic_adapter.py:136")
        text = "\n".join(
            view.render_detail(entry, "", NOW, view.Palette(True), 100, True, REPO, ROOTS)
        )
        self.assertNotIn("\x1b]8;;", text)
        self.assertIn("anthropic_adapter", view.plain(text))


class TestEndToEnd(unittest.TestCase):
    """`main` over `--file`, which is the whole tool minus the kubectl call."""

    def test_the_report_has_every_section(self):
        with ledger_file(ledger()) as path:
            code, text = run_main(["--file", path, "--color", "never", "--width", "150"])
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("last run"))
        for section in ("RUNS", "FINDINGS", "PULL REQUESTS OPENED"):
            self.assertIn(section, text)

    def test_the_pull_request_section_lists_what_the_ledger_holds(self):
        with ledger_file(ledger()) as path:
            _, text = run_main(["--file", path, "--color", "never", "--width", "150"])
        self.assertIn("gke-agentic/kube-agents#160", text)

    def test_the_pull_request_section_is_not_narrowed_by_a_severity_filter(self):
        """A --severity filter narrowing it would hide pull requests still open
        against the findings it hid, and "what has this loop opened" is a fact
        about the install rather than about the current filter."""
        with ledger_file(ledger()) as path:
            _, text = run_main(
                ["--file", path, "--color", "never", "--width", "150", "--severity", "critical"]
            )
        self.assertIn("gke-agentic/kube-agents#160", text)

    def test_an_empty_pull_request_list_explains_itself(self):
        document = ledger()
        for entry in document["findings"].values():
            entry["promotions"] = []
        with ledger_file(document) as path:
            _, text = run_main(["--file", path, "--color", "never"])
        self.assertIn("none recorded", text)

    def test_no_color_is_honoured_end_to_end(self):
        with ledger_file(ledger()) as path:
            with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False):
                _, text = run_main(["--file", path, "--width", "150"])
        self.assertNotIn("\033[", text)

    def test_ascii_mode_is_pipe_safe(self):
        with ledger_file(ledger()) as path:
            _, text = run_main(["--file", path, "--color", "never", "--ascii", "--width", "150"])
        self.assertFalse(any(ch in text for ch in "─│┌┬┐├┼┤└┴┘"))

    def test_json_prints_the_ledger_and_nothing_else(self):
        with ledger_file(ledger()) as path:
            code, text = run_main(["--file", path, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(text)["findings"]), 3)

    def test_json_unwraps_a_configmap_too(self):
        wrapped = {"data": {view.LEDGER_KEY: json.dumps(ledger())}}
        with ledger_file(wrapped) as path:
            _, text = run_main(["--file", path, "--json"])
        self.assertIn("findings", json.loads(text))

    def test_detail_by_row_number(self):
        with ledger_file(ledger()) as path:
            code, text = run_main(["--file", path, "--color", "never", "--detail", "1"])
        self.assertEqual(code, 0)
        self.assertIn("A critical finding", text)

    def test_detail_by_fingerprint_prefix(self):
        with ledger_file(ledger()) as path:
            code, text = run_main(["--file", path, "--color", "never", "--detail", "cccc"])
        self.assertEqual(code, 0)
        self.assertIn("A low finding", text)

    def test_detail_that_matches_nothing_fails_with_advice(self):
        with ledger_file(ledger()) as path:
            code, _ = run_main(["--file", path, "--detail", "zzzz"])
        self.assertEqual(code, 1)

    def test_a_file_that_is_not_a_ledger_is_rejected(self):
        with ledger_file({"something": "else"}) as path:
            code, _ = run_main(["--file", path])
        self.assertEqual(code, 1)

    def test_a_file_that_is_not_json_is_rejected_rather_than_traced(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("not json at all")
            path = handle.name
        try:
            code, _ = run_main(["--file", path])
        finally:
            os.unlink(path)
        self.assertEqual(code, 1)

    def test_a_missing_file_is_rejected_rather_than_traced(self):
        code, _ = run_main(["--file", "/nonexistent/ledger.json"])
        self.assertEqual(code, 1)

    def test_an_empty_ledger_renders_rather_than_crashing(self):
        with ledger_file({"version": 1, "findings": {}, "runs": []}) as path:
            code, text = run_main(["--file", path, "--color", "never", "--width", "150"])
        self.assertEqual(code, 0)
        self.assertIn("no runs recorded yet", text)
        self.assertIn("no findings match", text)

    def test_a_narrow_terminal_still_produces_aligned_tables_that_fit(self):
        """Each table is internally aligned and inside the budget. Different
        tables legitimately settle at different widths -- they have different
        columns -- so this groups the lines by table rather than demanding one
        width across the report."""
        with ledger_file(ledger()) as path:
            _, text = run_main(["--file", path, "--color", "never", "--width", "80"])
        for block in table_blocks(text):
            widths = {len(line) for line in block}
            self.assertEqual(len(widths), 1, "a table's lines disagree on width: %s" % sorted(widths))
            self.assertLessEqual(widths.pop(), 80)

    def test_a_narrow_terminal_says_which_columns_it_dropped(self):
        with ledger_file(ledger()) as path:
            _, text = run_main(["--file", path, "--color", "never", "--width", "80"])
        self.assertIn("dropped to fit 80 columns", text)

    def test_a_wide_terminal_drops_nothing(self):
        with ledger_file(ledger()) as path:
            _, text = run_main(["--file", path, "--color", "never", "--width", "170"])
        self.assertNotIn("dropped to fit", text)
        for header in ("SIGNAL", "CONF", "REPORTED", "REVISION"):
            self.assertIn(header, text)

    def test_the_file_path_never_shells_out(self):
        """`--file` is the offline door; a stray kubectl call on it would make
        the tests depend on a cluster and the tool unusable on a plane."""
        with ledger_file(ledger()) as path:
            with mock.patch("subprocess.run", side_effect=AssertionError("kubectl was called")):
                code, _ = run_main(["--file", path, "--color", "never"])
        self.assertEqual(code, 0)


class TestArgumentSurface(unittest.TestCase):
    def test_the_defaults_match_what_the_chart_installs(self):
        args = view.build_parser().parse_args([])
        self.assertEqual(args.namespace, "kubeagents-system")
        self.assertEqual(args.configmap, "kube-agents-selfimprove-ledger")
        self.assertEqual(args.cronjob, "kube-agents-selfimprove")

    def test_the_namespace_and_configmap_can_come_from_the_environment(self):
        with mock.patch.dict(
            os.environ,
            {"SELFIMPROVE_NAMESPACE": "other", "SELFIMPROVE_LEDGER_CONFIGMAP": "other-cm"},
            clear=False,
        ):
            args = view.build_parser().parse_args([])
        self.assertEqual(args.namespace, "other")
        self.assertEqual(args.configmap, "other-cm")

    def test_the_severity_choices_are_the_ledgers_own(self):
        if view.ledger_mod is None:
            self.skipTest("selfimprove_ledger is not importable from this checkout")
        self.assertEqual(view.SEVERITY_ORDER, view.ledger_mod.SEVERITIES)

    def test_the_size_cap_matches_the_ledgers_own(self):
        if view.ledger_mod is None:
            self.skipTest("selfimprove_ledger is not importable from this checkout")
        self.assertEqual(view.FALLBACK_MAX_BYTES, view.ledger_mod.LEDGER_MAX_BYTES)

    def test_the_ledger_key_matches_the_ledgers_own(self):
        if view.ledger_mod is None:
            self.skipTest("selfimprove_ledger is not importable from this checkout")
        self.assertEqual(view.LEDGER_KEY, view.ledger_mod.LEDGER_KEY)

    def test_the_script_is_executable(self):
        path = pathlib.Path(view.__file__)
        self.assertTrue(os.access(path, os.X_OK), "%s is not executable" % path)


if __name__ == "__main__":
    unittest.main()
