#!/usr/bin/env python3
"""Tests for the self-improvement ledger, fingerprint and promotion gate.

The gate is the part of this feature that decides whether an autonomous agent
opens a pull request on a human's repository, so it is the part that has to be
right when nobody is watching. All of it is pure -- no Kubernetes import at
module scope in selfimprove_ledger.py -- which is what lets these run in CI with
no cluster.

Every test that involves time passes `now` explicitly. A test that let the clock
run would be a test whose failure depends on when it is run, and the window
arithmetic here is exactly the kind of thing that fails at midnight.
"""

import datetime as dt
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import selfimprove_ledger as L  # noqa: E402


NOW = dt.datetime(2026, 8, 22, 12, 0, 0, tzinfo=dt.timezone.utc)


def finding(**overrides):
    base = {
        "signal": "errors",
        "severity": "high",
        "title": "Reconciler retries a Secret it cannot read",
        "location": "k8s-operator/internal/controller/platformagent_controller.go:412",
        "summary": "The reconcile loop retries forever.",
        "evidence": ["2026-08-22T09:00:00Z E0822 secrets is forbidden"],
        "proposed_fix": "Fail the reconcile with a clear status condition.",
    }
    base.update(overrides)
    return base


def gate(**overrides):
    base = {
        "rules": [
            {"severity": "critical", "minOccurrencesPerDay": 1},
            {"severity": "high", "minOccurrencesPerDay": 5},
        ],
        "maxPullRequestsPerDay": 2,
        "cooldownHours": 24,
    }
    base.update(overrides)
    return base


class NormaliseTests(unittest.TestCase):
    def test_strips_the_parts_that_differ_between_sightings(self):
        a = L.normalise("pod platform-agent-gateway-7d9f4c8b6-xk2vn failed at 2026-08-22T09:14:03Z")
        b = L.normalise("pod platform-agent-gateway-5a1c2d3e4-pq8zt failed at 2026-08-22T11:47:51Z")
        self.assertEqual(a, b)

    def test_keeps_genuinely_different_text_different(self):
        self.assertNotEqual(
            L.normalise("reconciler cannot read Secret"),
            L.normalise("reconciler cannot read ConfigMap"),
        )

    def test_a_title_keeps_its_digits(self):
        """The digit sweep is scoped to locations, and this is why.

        "skill 1 fails" and "skill 2 fails" are two bugs. Collapsing them gives
        one entry whose occurrence count is their sum -- and the count is what
        the gate reads, so over-normalising a title does not lose information
        so much as manufacture a promotion out of two unrelated sightings.
        """
        self.assertNotEqual(
            L.normalise("github-issue-resolver retry 1 exhausted"),
            L.normalise("github-issue-resolver retry 2 exhausted"),
        )


class NormaliseLocationTests(unittest.TestCase):
    def test_line_numbers_collapse(self):
        """A line number drifts on every commit that touches the file above it.

        Without this the same bug fingerprints differently after an unrelated
        import is added, its count resets to one, and a `critical` that should
        have been filed on the second sighting never clears the gate.
        """
        self.assertEqual(
            L.normalise_location("k8s-operator/internal/controller/platformagent.go:412"),
            L.normalise_location("k8s-operator/internal/controller/platformagent.go:418"),
        )

    def test_the_file_still_distinguishes(self):
        self.assertNotEqual(
            L.normalise_location("gateway.py:12"), L.normalise_location("runner.py:12")
        )

    def test_a_column_is_collapsed_with_the_line(self):
        self.assertEqual(
            L.normalise_location("selfimprove_run.py:88:14"),
            L.normalise_location("selfimprove_run.py:91:3"),
        )


class FingerprintTests(unittest.TestCase):
    def test_is_stable_across_incidental_variation(self):
        first = L.fingerprint("errors", "Gateway timed out at 2026-08-22T09:00:00Z", "gateway.py:12")
        second = L.fingerprint("errors", "Gateway timed out at 2026-08-22T18:31:44Z", "gateway.py:12")
        self.assertEqual(first, second)

    def test_severity_is_not_part_of_identity(self):
        """A re-graded finding is the same finding.

        This is the property that keeps occurrence counts accumulating when the
        agent changes its mind about how bad something is. Without it, the third
        sighting of a bug graded `high`, `critical`, `high` looks like three
        separate findings with one sighting each and nothing is ever promoted.
        """
        one, _ = L.record_finding(L.empty_ledger(), finding(severity="high"), "abc", NOW)
        two, _ = L.record_finding(L.empty_ledger(), finding(severity="critical"), "abc", NOW)
        self.assertEqual(one, two)

    def test_signal_and_location_are_part_of_identity(self):
        base = L.fingerprint("errors", "Same title", "a.py:1")
        self.assertNotEqual(base, L.fingerprint("latency", "Same title", "a.py:1"))
        self.assertNotEqual(base, L.fingerprint("errors", "Same title", "b.py:1"))


class RecordFindingTests(unittest.TestCase):
    def test_the_agent_cannot_set_its_own_history(self):
        """Identity fields the agent supplies are honoured; history is not.

        An agent that could write its own occurrence count could talk itself
        past the gate in a single run, which would make the frequency half of
        the severity/frequency contract meaningless.
        """
        ledger = L.empty_ledger()
        _, entry = L.record_finding(
            ledger,
            finding(promotions=[{"at": L.to_iso(NOW), "url": "https://example.invalid/pr/1"}]),
            "abc123",
            NOW,
        )
        self.assertEqual(entry["promotions"], [])

    def test_unknown_signal_and_severity_fall_back_rather_than_raising(self):
        _, entry = L.record_finding(
            L.empty_ledger(), finding(signal="wishlist", severity="catastrophic"), "abc", NOW
        )
        self.assertEqual(entry["signal"], "other")
        # `low`, not `critical`: an unparseable grade must not buy priority.
        self.assertEqual(entry["severity"], "low")

    def test_repeat_sightings_accumulate_on_one_entry(self):
        ledger = L.empty_ledger()
        L.record_finding(ledger, finding(occurrences=3), "abc", NOW - dt.timedelta(hours=2))
        fp, entry = L.record_finding(ledger, finding(occurrences=4), "abc", NOW)
        self.assertEqual(len(ledger["findings"]), 1)
        # Two runs saw it, so the gate's number is 2 -- not the 7 the two runs
        # said they counted between them, which is evidence and kept separately.
        self.assertEqual(L.occurrences_in_window(entry, NOW), 2)
        self.assertEqual(L.reported_occurrences_in_window(entry, NOW), 7)
        self.assertEqual(entry["first_seen"], L.to_iso(NOW - dt.timedelta(hours=2)))
        self.assertEqual(entry["last_seen"], L.to_iso(NOW))

    def test_a_fingerprint_the_agent_supplies_is_ignored(self):
        """The agent cannot choose its own identity.

        Echoing back an existing fingerprint would inherit that finding's
        accumulated sightings and promote on the first appearance; inventing a
        fresh one each run would keep the count at 1 forever. Both are
        gate-steering, and both are closed by recomputing.
        """
        ledger = L.empty_ledger()
        honest = finding()
        computed = L.fingerprint(honest["signal"], honest["title"], honest["location"])
        fp, entry = L.record_finding(
            ledger, finding(fingerprint="deadbeefdeadbeef"), "abc", NOW
        )
        self.assertEqual(fp, computed)
        self.assertEqual(entry["fingerprint"], computed)
        self.assertNotIn("deadbeefdeadbeef", ledger["findings"])

    def test_one_run_reporting_a_finding_five_times_is_one_sighting(self):
        """The whole defence, stated as a test.

        `minOccurrencesPerDay: 5` has to mean five investigations. A single run
        that emits the same finding five times -- which log text telling the
        agent to do so could arrange -- must not clear it.
        """
        ledger = L.empty_ledger()
        for _ in range(5):
            fp, entry = L.record_finding(ledger, finding(occurrences=1), "abc", NOW)
        self.assertEqual(len(entry["sightings"]), 1)
        self.assertEqual(L.occurrences_in_window(entry, NOW), 1)
        promoted, reasons = L.evaluate_gate(ledger, gate(), [fp] * 5, NOW)
        self.assertEqual(promoted, [])
        self.assertIn("rule wants 5", reasons[fp])


class OccurrenceWindowTests(unittest.TestCase):
    def test_counts_only_inside_the_window(self):
        entry = {
            "sightings": [
                {"at": L.to_iso(NOW - dt.timedelta(hours=30)), "count": 100},
                {"at": L.to_iso(NOW - dt.timedelta(hours=2)), "count": 3},
            ]
        }
        # One sighting inside the window, so one -- the stale sighting's 100 is
        # out of the window and the fresh one's 3 is not the gate's business.
        self.assertEqual(L.occurrences_in_window(entry, NOW), 1)
        self.assertEqual(L.reported_occurrences_in_window(entry, NOW), 3)

    def test_a_huge_agent_supplied_count_does_not_move_the_gate(self):
        entry = {"sightings": [{"at": L.to_iso(NOW), "count": 10_000}]}
        self.assertEqual(L.occurrences_in_window(entry, NOW), 1)
        self.assertEqual(L.reported_occurrences_in_window(entry, NOW), 10_000)

    def test_a_malformed_timestamp_is_ignored_not_counted(self):
        """An unparseable sighting withholds a promotion rather than granting one."""
        entry = {"sightings": [{"at": "not a date", "count": 99}]}
        self.assertEqual(L.occurrences_in_window(entry, NOW), 0)
        self.assertEqual(L.reported_occurrences_in_window(entry, NOW), 0)

    def test_the_schedule_is_a_ceiling_on_the_threshold(self):
        """Where the shipped `high` rule stops being reachable at all.

        One occurrence per run means the CronJob's interval bounds what any
        threshold can ever count. Five sightings span four intervals and the
        window is 24 hours, so six-hourly is the slowest schedule on which a
        threshold of 5 is satisfiable -- an operator who sets `schedule` slower
        disables that severity, and nothing tells them. Design sec. 12 states
        this boundary; this is the arithmetic behind it.
        """
        for interval, reachable in ((1, True), (6, True), (7, False), (24, False)):
            with self.subTest(interval=interval):
                entry = {
                    "sightings": [
                        {"at": L.to_iso(NOW - dt.timedelta(hours=interval * n)), "count": 1}
                        for n in range(5)
                    ]
                }
                self.assertEqual(L.occurrences_in_window(entry, NOW) >= 5, reachable)


class GateTests(unittest.TestCase):
    def _ledger_with(self, runs, **kw):
        """A ledger where `runs` separate investigations saw the same finding.

        One sighting per hour, ending now: that is what the gate counts, so a
        test that wants to clear `minOccurrencesPerDay: 5` has to arrange five
        runs rather than one run claiming five.
        """
        ledger = L.empty_ledger()
        fp = ""
        for age in range(runs - 1, -1, -1):
            fp, _ = L.record_finding(ledger, finding(**kw), "abc", NOW - dt.timedelta(hours=age))
        return ledger, fp

    def test_promotes_when_severity_and_frequency_are_both_met(self):
        ledger, fp = self._ledger_with(6)
        promoted, reasons = L.evaluate_gate(ledger, gate(), [fp], NOW)
        self.assertEqual(promoted, [fp])
        self.assertIn("promoted", reasons[fp])

    def test_holds_when_frequency_is_short(self):
        ledger, fp = self._ledger_with(4)  # rule wants 5
        promoted, reasons = L.evaluate_gate(ledger, gate(), [fp], NOW)
        self.assertEqual(promoted, [])
        self.assertIn("rule wants 5", reasons[fp])

    def test_a_severity_with_no_rule_is_never_promoted(self):
        """`medium` and `low` are excluded by omission, not by a separate switch."""
        ledger, fp = self._ledger_with(10_000, severity="medium")
        promoted, reasons = L.evaluate_gate(ledger, gate(), [fp], NOW)
        self.assertEqual(promoted, [])
        self.assertIn("no promotion rule", reasons[fp])

    def test_critical_clears_on_a_single_occurrence(self):
        ledger, fp = self._ledger_with(1, severity="critical")
        promoted, _ = L.evaluate_gate(ledger, gate(), [fp], NOW)
        self.assertEqual(promoted, [fp])

    def test_cooldown_blocks_a_refile(self):
        ledger, fp = self._ledger_with(9)
        L.record_promotion(ledger, fp, "https://example.invalid/pr/1", "abc", NOW - dt.timedelta(hours=3))
        promoted, reasons = L.evaluate_gate(ledger, gate(), [fp], NOW)
        self.assertEqual(promoted, [])
        self.assertIn("cooldown", reasons[fp])

    def test_cooldown_expires(self):
        ledger, fp = self._ledger_with(9)
        L.record_promotion(ledger, fp, "https://example.invalid/pr/1", "abc", NOW - dt.timedelta(hours=25))
        promoted, _ = L.evaluate_gate(ledger, gate(), [fp], NOW)
        self.assertEqual(promoted, [fp])

    def test_the_daily_budget_caps_one_run(self):
        """Six criticals in one run must not become six pull requests.

        The budget is decremented inside the loop rather than only compared
        against the ledger, because nothing has been filed yet when the second
        candidate is considered.
        """
        ledger = L.empty_ledger()
        fps = []
        for i in range(6):
            fp, _ = L.record_finding(
                ledger,
                finding(severity="critical", title="Critical number %d" % i, location="a.py:%d" % i),
                "abc",
                NOW,
            )
            fps.append(fp)
        promoted, reasons = L.evaluate_gate(ledger, gate(), fps, NOW)
        self.assertEqual(len(promoted), 2)
        self.assertEqual(sum(1 for r in reasons.values() if "budget" in r), 4)

    def test_the_budget_spans_runs_not_just_one_run(self):
        ledger = L.empty_ledger()
        old, _ = L.record_finding(ledger, finding(title="Earlier", location="a.py:1"), "abc", NOW)
        L.record_promotion(ledger, old, "https://example.invalid/pr/1", "abc", NOW - dt.timedelta(hours=1))
        L.record_promotion(ledger, old, "https://example.invalid/pr/2", "abc", NOW - dt.timedelta(hours=2))
        fresh, _ = L.record_finding(
            ledger, finding(severity="critical", title="Now", location="b.py:1"), "abc", NOW
        )
        promoted, reasons = L.evaluate_gate(ledger, gate(), [fresh], NOW)
        self.assertEqual(promoted, [])
        self.assertIn("budget", reasons[fresh])

    def test_worse_severities_are_considered_first_under_a_tight_budget(self):
        ledger = L.empty_ledger()
        high, _ = L.record_finding(
            ledger, finding(severity="high", occurrences=50, title="High", location="a.py:1"), "abc", NOW
        )
        critical, _ = L.record_finding(
            ledger, finding(severity="critical", occurrences=1, title="Critical", location="b.py:1"), "abc", NOW
        )
        promoted, _ = L.evaluate_gate(ledger, gate(maxPullRequestsPerDay=1), [high, critical], NOW)
        self.assertEqual(promoted, [critical])

    def test_an_empty_gate_promotes_nothing(self):
        """The failure mode of a misrendered or missing config must be silence."""
        ledger, fp = self._ledger_with(10_000, severity="critical")
        promoted, _ = L.evaluate_gate(ledger, {}, [fp], NOW)
        self.assertEqual(promoted, [])


class CooldownSanitisingTests(unittest.TestCase):
    """`cooldownHours` from a human's values.yaml, in every shape it arrives in.

    These exist because the gate and `prune` used to read it separately and
    reach different answers. The parametrised cases below assert the reading;
    `test_a_negative_cooldown_still_holds_a_promoted_finding` asserts the
    consequence, which is the one that was actually wrong -- a gate that has
    stopped holding anything looks exactly like a gate doing its job until the
    duplicate pull requests arrive.
    """

    def test_usable_values_pass_through_without_a_note(self):
        for raw in (0, 1, 24.5, "48", L.MAX_COOLDOWN_HOURS):
            with self.subTest(raw=raw):
                hours, note = L.sanitise_cooldown_hours(raw)
                self.assertEqual(hours, float(raw))
                self.assertIsNone(note)

    def test_unusable_values_fall_back_to_the_window_and_say_so(self):
        for raw in (float("inf"), float("-inf"), float("nan"), -5, "soon", None, {}):
            with self.subTest(raw=raw):
                hours, note = L.sanitise_cooldown_hours(raw)
                self.assertEqual(hours, float(L.COUNT_WINDOW_HOURS))
                self.assertIsNotNone(note)

    def test_an_enormous_finite_value_is_clamped_rather_than_replaced(self):
        """"Never re-file" is a coherent instruction; 1e8 hours is not a date."""
        hours, note = L.sanitise_cooldown_hours(1e8)
        self.assertEqual(hours, float(L.MAX_COOLDOWN_HOURS))
        self.assertIn("ceiling", note)

    def test_no_supplied_value_survives_into_datetime_arithmetic(self):
        """Every shape above reaches both callers without raising.

        `timedelta(hours=inf)` raises `OverflowError` and `timedelta(hours=nan)`
        raises `ValueError`, both from inside the gate; a large finite value
        walks `prune`'s subtraction off the bottom of `datetime`. None of the
        three is caught anywhere upstream, so each one killed the run.
        """
        ledger = L.empty_ledger()
        fp, _ = L.record_finding(ledger, finding(severity="critical"), "abc", NOW)
        L.record_promotion(ledger, fp, "https://example/1", "abc", NOW)
        for raw in (float("inf"), float("nan"), -5, 1e8, 1e400, "soon"):
            with self.subTest(raw=raw):
                hours, _ = L.sanitise_cooldown_hours(raw)
                L.prune(L.clone(ledger), NOW, cooldown_hours=hours)
                L.evaluate_gate(L.clone(ledger), gate(cooldownHours=raw), [fp], NOW)

    def test_a_negative_cooldown_still_holds_a_promoted_finding(self):
        """The defect this class was written for.

        A negative timedelta is never greater than an elapsed one, so the gate's
        cooldown check passed for everything and the loop re-filed a finding it
        had filed an hour earlier -- against a ceiling that counts promotions,
        so nothing else stopped it either.
        """
        ledger = L.empty_ledger()
        fp, _ = L.record_finding(ledger, finding(severity="critical"), "abc", NOW)
        L.record_promotion(ledger, fp, "https://example/1", "abc", NOW - dt.timedelta(hours=1))
        promoted, reasons = L.evaluate_gate(
            ledger, gate(cooldownHours=-5), [fp], NOW
        )
        self.assertEqual(promoted, [])
        self.assertIn("cooldown", reasons[fp])

    def test_prune_and_the_gate_agree_on_what_the_cooldown_is(self):
        """They read the same value through the same function, by construction.

        Asserted rather than assumed: the bug was two call sites, and a future
        edit that gives either one its own parsing again reintroduces it.
        """
        for raw in (float("inf"), -5, 1e8, "soon", 72):
            with self.subTest(raw=raw):
                self.assertEqual(
                    L.sanitise_cooldown_hours(raw)[0],
                    L.sanitise_cooldown_hours(gate(cooldownHours=raw)["cooldownHours"])[0],
                )


class GateCountSanitisingTests(unittest.TestCase):
    """`maxPullRequestsPerDay` and `minOccurrencesPerDay`, in every shape.

    The same class of defect as the cooldown above and in the same function:
    both were read with an `int()` guarded by `except (TypeError, ValueError)`,
    which does not catch the `OverflowError` that `int(float("inf"))` raises.
    `.inf` is how YAML spells infinity and both keys have an intent it plausibly
    expresses, so the run died at the gate -- after the investigation was
    already paid for -- and again on the hour, every hour.
    """

    def test_usable_values_pass_through_without_a_note(self):
        for raw in (0, 1, 5, "12", 2.0, L.MAX_GATE_COUNT):
            with self.subTest(raw=raw):
                value, note = L.sanitise_gate_count(raw, "k", 7)
                self.assertEqual(value, int(float(raw)))
                self.assertIsNone(note)

    def test_values_that_are_not_numbers_fall_back_to_the_default(self):
        for raw in (None, "often", {}, float("nan")):
            with self.subTest(raw=raw):
                value, note = L.sanitise_gate_count(raw, "k", 7)
                self.assertEqual(value, 7)
                self.assertIn("not a number", note)

    def test_infinity_is_clamped_rather_than_replaced(self):
        """"No ceiling" and "never" are both coherent; neither survives `int()`."""
        for raw in (float("inf"), 1e400, 1e9):
            with self.subTest(raw=raw):
                value, note = L.sanitise_gate_count(raw, "k", 7)
                self.assertEqual(value, L.MAX_GATE_COUNT)
                self.assertIn("ceiling", note)

    def test_a_negative_count_clamps_to_zero_and_says_so(self):
        value, note = L.sanitise_gate_count(-1, "maxPullRequestsPerDay", 0)
        self.assertEqual(value, 0)
        self.assertIn("negative", note)

    def test_an_infinite_budget_files_instead_of_killing_the_run(self):
        """The defect: `maxPullRequestsPerDay: .inf` raised out of `evaluate_gate`.

        An operator writing it means "no ceiling", so the fix has to deliver
        that -- falling back to the default of 0 would read as a crash averted
        while silently filing nothing.
        """
        ledger = L.empty_ledger()
        fps = []
        for i in range(6):
            fp, _ = L.record_finding(
                ledger,
                finding(severity="critical", title="Critical %d" % i, location="a.py:%d" % i),
                "abc",
                NOW,
            )
            fps.append(fp)
        promoted, _ = L.evaluate_gate(
            ledger, gate(maxPullRequestsPerDay=float("inf")), fps, NOW
        )
        self.assertEqual(len(promoted), 6)

    def test_an_infinite_threshold_holds_instead_of_killing_the_run(self):
        ledger = L.empty_ledger()
        fp, _ = L.record_finding(ledger, finding(severity="critical"), "abc", NOW)
        rules = [{"severity": "critical", "minOccurrencesPerDay": float("inf")}]
        promoted, reasons = L.evaluate_gate(
            ledger, gate(rules=rules), [fp], NOW
        )
        self.assertEqual(promoted, [])
        self.assertIn("rule wants", reasons[fp])

    def test_no_supplied_value_raises_out_of_the_gate(self):
        ledger = L.empty_ledger()
        fp, _ = L.record_finding(ledger, finding(severity="critical"), "abc", NOW)
        for raw in (float("inf"), float("nan"), 1e400, -1, "lots", None, {}):
            with self.subTest(raw=raw):
                L.evaluate_gate(
                    L.clone(ledger), gate(maxPullRequestsPerDay=raw), [fp], NOW
                )
                L.evaluate_gate(
                    L.clone(ledger),
                    gate(rules=[{"severity": "critical", "minOccurrencesPerDay": raw}]),
                    [fp],
                    NOW,
                )

    def test_gate_notes_reports_exactly_what_the_gate_used(self):
        """The runner logs these; a note the gate does not act on would mislead."""
        self.assertEqual(L.gate_notes(gate()), [])
        notes = L.gate_notes(
            gate(
                maxPullRequestsPerDay=float("inf"),
                cooldownHours=-5,
                rules=[{"severity": "critical", "minOccurrencesPerDay": "many"}],
            )
        )
        self.assertEqual(len(notes), 3)
        self.assertTrue(any("maxPullRequestsPerDay" in n for n in notes))
        self.assertTrue(any("cooldownHours" in n for n in notes))
        self.assertTrue(any("minOccurrencesPerDay (severity critical)" in n for n in notes))

    def test_gate_notes_survives_a_malformed_rules_list(self):
        for rules in (None, "critical", ["critical"], [None]):
            with self.subTest(rules=rules):
                L.gate_notes({"rules": rules})


class PruneTests(unittest.TestCase):
    def test_drops_stale_findings_but_keeps_recently_promoted_ones(self):
        ledger = L.empty_ledger()
        stale, _ = L.record_finding(
            ledger, finding(title="Stale", location="a.py:1"), "abc", NOW - dt.timedelta(days=45)
        )
        filed, _ = L.record_finding(
            ledger, finding(title="Filed", location="b.py:1"), "abc", NOW - dt.timedelta(days=45)
        )
        L.record_promotion(ledger, filed, "https://example.invalid/pr/1", "abc", NOW - dt.timedelta(days=2))
        L.prune(ledger, NOW)
        self.assertNotIn(stale, ledger["findings"])
        # Kept because forgetting it would let the loop re-file a pull request
        # that already exists.
        self.assertIn(filed, ledger["findings"])

    def test_a_long_ago_promotion_stops_holding_the_row_open(self):
        """The ledger's only unbounded growth path, closed.

        A promoted row used to be exempt from deletion forever, so at the
        default two pull requests a day an install accumulated rows until the
        serialised ledger passed LEDGER_MAX_BYTES -- after which every run
        failed at the write, which is where a run's findings become durable.
        """
        ledger = L.empty_ledger()
        old, _ = L.record_finding(ledger, finding(title="Ancient"), "abc", NOW - dt.timedelta(days=400))
        L.record_promotion(ledger, old, "https://example.invalid/pr/1", "abc", NOW - dt.timedelta(days=399))
        L.prune(ledger, NOW)
        self.assertNotIn(old, ledger["findings"])

    def test_a_cooldown_longer_than_the_retention_wins(self):
        """Retention must never drop a promotion the cooldown still consults.

        An install with a 90-day cooldown that pruned on the 30-day default
        would re-file a finding it filed 40 days ago, which is exactly what the
        promotion record exists to prevent.
        """
        ledger = L.empty_ledger()
        fp, _ = L.record_finding(ledger, finding(title="Filed"), "abc", NOW - dt.timedelta(days=45))
        L.record_promotion(ledger, fp, "https://example.invalid/pr/1", "abc", NOW - dt.timedelta(days=40))
        L.prune(ledger, NOW, cooldown_hours=90 * 24)
        self.assertIn(fp, ledger["findings"])
        # ... and the same ledger under the default cooldown does drop it.
        L.prune(ledger, NOW)
        self.assertNotIn(fp, ledger["findings"])

    def test_an_unparseable_promotion_date_holds_the_row(self):
        ledger = L.empty_ledger()
        fp, _ = L.record_finding(ledger, finding(title="Filed"), "abc", NOW - dt.timedelta(days=400))
        ledger["findings"][fp]["promotions"] = [{"at": "not-a-date", "url": "u", "revision": "abc"}]
        L.prune(ledger, NOW)
        self.assertIn(fp, ledger["findings"])

    def test_a_row_still_being_seen_survives_whatever_its_promotions_say(self):
        ledger = L.empty_ledger()
        fp, _ = L.record_finding(ledger, finding(title="Live"), "abc", NOW)
        L.record_promotion(ledger, fp, "https://example.invalid/pr/1", "abc", NOW - dt.timedelta(days=400))
        L.prune(ledger, NOW)
        self.assertIn(fp, ledger["findings"])

    def test_drops_sightings_outside_the_window(self):
        ledger = L.empty_ledger()
        fp, _ = L.record_finding(ledger, finding(occurrences=5), "abc", NOW - dt.timedelta(hours=48))
        L.record_finding(ledger, finding(occurrences=2), "abc", NOW)
        L.prune(ledger, NOW)
        self.assertEqual(len(ledger["findings"][fp]["sightings"]), 1)

    def test_promotions_are_bounded_and_the_newest_survives(self):
        """A promoted entry outlives the sighting window, so its promotion list
        would otherwise grow for as long as the row does."""
        ledger = L.empty_ledger()
        fp, _ = L.record_finding(ledger, finding(), "abc", NOW)
        for day in range(L.MAX_PROMOTIONS + 5):
            L.record_promotion(
                ledger,
                fp,
                "https://example.invalid/pr/%d" % day,
                "abc",
                NOW - dt.timedelta(days=L.MAX_PROMOTIONS + 5 - day),
            )
        L.prune(ledger, NOW)
        promotions = ledger["findings"][fp]["promotions"]
        self.assertEqual(len(promotions), L.MAX_PROMOTIONS)
        # The cooldown check reads the most recent one, so it is the end that
        # must survive trimming.
        self.assertEqual(
            promotions[-1]["url"],
            "https://example.invalid/pr/%d" % (L.MAX_PROMOTIONS + 4),
        )


class EntrySizeTests(unittest.TestCase):
    """The ledger is a ConfigMap. One run's evidence must not be able to fill it,
    because `save` raises on an over-cap ledger and every later run then records
    nothing at all."""

    def test_ordinary_evidence_is_stored_unchanged(self):
        ledger = L.empty_ledger()
        fp, entry = L.record_finding(ledger, finding(), "abc", NOW)
        self.assertEqual(entry["evidence"], finding()["evidence"])
        self.assertEqual(entry["summary"], finding()["summary"])

    def test_an_oversized_evidence_array_is_dropped_not_half_serialised(self):
        ledger = L.empty_ledger()
        huge = ["x" * 4096 for _ in range(20)]
        _, entry = L.record_finding(ledger, finding(evidence=huge), "abc", NOW)
        self.assertIsInstance(entry["evidence"], str)
        self.assertIn("over the", entry["evidence"])

    def test_an_oversized_summary_is_truncated_and_says_so(self):
        ledger = L.empty_ledger()
        _, entry = L.record_finding(
            ledger, finding(summary="y" * (L.MAX_ENTRY_TEXT_BYTES + 500)), "abc", NOW
        )
        self.assertIn("[truncated:", entry["summary"])
        self.assertLess(len(entry["summary"]), L.MAX_ENTRY_TEXT_BYTES + 200)

    def test_title_and_location_are_capped_too(self):
        """The two agent-supplied fields that used to be stored raw.

        A handful of findings with megabyte titles is enough to push the ledger
        past LEDGER_MAX_BYTES, at which point `save` raises and every later run
        loses its output.
        """
        ledger = L.empty_ledger()
        _, entry = L.record_finding(
            ledger,
            finding(title="t" * 5000, location="l" * 9000),
            "abc",
            NOW,
        )
        self.assertEqual(len(entry["title"]), L.MAX_TITLE_CHARS)
        self.assertEqual(len(entry["location"]), L.MAX_LOCATION_CHARS)
        self.assertTrue(entry["title"].endswith("…"))

    def test_the_fingerprint_is_taken_over_the_stored_title(self):
        """Cap first, fingerprint second.

        Fingerprinting the raw title would give two findings that differ only
        past the cap two identities and one stored row, so the row's count would
        never rise and the gate would never see it.
        """
        ledger = L.empty_ledger()
        long_a = "t" * L.MAX_TITLE_CHARS + "aaaa"
        long_b = "t" * L.MAX_TITLE_CHARS + "bbbb"
        fp_a, entry_a = L.record_finding(ledger, finding(title=long_a), "abc", NOW)
        fp_b, _ = L.record_finding(ledger, finding(title=long_b), "abc", NOW)
        self.assertEqual(fp_a, fp_b)
        self.assertEqual(fp_a, L.fingerprint(entry_a["signal"], entry_a["title"], entry_a["location"]))

    def test_a_short_title_is_left_exactly_alone(self):
        ledger = L.empty_ledger()
        _, entry = L.record_finding(ledger, finding(title="Reconciler retries"), "abc", NOW)
        self.assertEqual(entry["title"], "Reconciler retries")

    def test_the_cap_does_not_split_a_multibyte_character(self):
        ledger = L.empty_ledger()
        _, entry = L.record_finding(ledger, finding(title="é" * 5000), "abc", NOW)
        # Encodes cleanly, which a byte-wise cut through a two-byte character
        # would not -- and the fingerprint depends on this string.
        entry["title"].encode("utf-8")
        self.assertEqual(len(entry["title"]), L.MAX_TITLE_CHARS)

    def test_the_bounded_entry_still_round_trips_as_json(self):
        ledger = L.empty_ledger()
        L.record_finding(ledger, finding(evidence=["z" * 40000]), "abc", NOW)
        self.assertEqual(
            json.loads(json.dumps(ledger))["findings"].keys(), ledger["findings"].keys()
        )


class CoerceTests(unittest.TestCase):
    def test_nonsense_becomes_an_empty_ledger_rather_than_an_exception(self):
        """The run that would have rewritten a corrupt ledger is the one that crashes on it."""
        for junk in (None, [], "", 7, {"findings": "not a dict"}):
            self.assertEqual(L.coerce(junk)["findings"], {})

    def test_run_history_is_bounded(self):
        raw = {"findings": {}, "runs": [{"n": i} for i in range(L.RUN_HISTORY + 20)]}
        self.assertEqual(len(L.coerce(raw)["runs"]), L.RUN_HISTORY)


class SummaryTests(unittest.TestCase):
    def test_the_brief_carries_the_fingerprint_the_agent_must_reuse(self):
        ledger = L.empty_ledger()
        fp, _ = L.record_finding(ledger, finding(occurrences=3), "abc", NOW)
        text = L.summarise_for_prompt(ledger, NOW)
        self.assertIn(fp, text)
        self.assertIn("Reconciler retries a Secret it cannot read", text)

    def test_an_empty_ledger_says_so_rather_than_rendering_a_blank(self):
        self.assertTrue(L.summarise_for_prompt(L.empty_ledger(), NOW).strip())

    def test_the_brief_carries_the_location_identity_depends_on(self):
        """`fingerprint` hashes title AND location. A summary showing only the
        title asks the next run to reuse something it was never shown, so the
        fingerprint differs, the count restarts, and nothing ever promotes."""
        ledger = L.empty_ledger()
        L.record_finding(ledger, finding(), "abc", NOW)
        self.assertIn(finding()["location"], L.summarise_for_prompt(ledger, NOW))


class AgentGradedFieldTests(unittest.TestCase):
    """SOUL.md asks the agent for `confidence` and `user_impact`; both were
    collected and then dropped on the floor by `record_finding`."""

    def test_both_fields_survive_into_the_entry(self):
        ledger = L.empty_ledger()
        _, entry = L.record_finding(
            ledger,
            dict(finding(), confidence="low", user_impact="Users see a truncated reply."),
            "abc",
            NOW,
        )
        self.assertEqual(entry["confidence"], "low")
        self.assertEqual(entry["user_impact"], "Users see a truncated reply.")

    def test_an_unrecognised_confidence_is_not_guessed_at(self):
        """Normalising to a default would put a grade in front of a reviewer
        that the investigation never claimed."""
        ledger = L.empty_ledger()
        for value in ("very high", "", "  ", None):
            _, entry = L.record_finding(ledger, dict(finding(), confidence=value), "abc", NOW)
            self.assertEqual(entry["confidence"], "")

    def test_confidence_is_case_insensitive(self):
        ledger = L.empty_ledger()
        _, entry = L.record_finding(ledger, dict(finding(), confidence="HIGH"), "abc", NOW)
        self.assertEqual(entry["confidence"], "high")

    def test_user_impact_is_bounded_like_the_other_agent_text(self):
        ledger = L.empty_ledger()
        _, entry = L.record_finding(
            ledger,
            dict(finding(), user_impact="x" * (L.MAX_ENTRY_TEXT_BYTES + 500)),
            "abc",
            NOW,
        )
        self.assertLess(len(entry["user_impact"]), L.MAX_ENTRY_TEXT_BYTES + 200)
        self.assertIn("truncated", entry["user_impact"])

    def test_the_brief_states_confidence_even_when_the_run_did_not(self):
        ledger = L.empty_ledger()
        L.record_finding(ledger, finding(), "abc", NOW)
        self.assertIn("conf=unstated", L.summarise_for_prompt(ledger, NOW))

    def test_the_brief_carries_a_stated_confidence(self):
        ledger = L.empty_ledger()
        L.record_finding(ledger, dict(finding(), confidence="medium"), "abc", NOW)
        self.assertIn("conf=medium", L.summarise_for_prompt(ledger, NOW))


if __name__ == "__main__":
    unittest.main()
