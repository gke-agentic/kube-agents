"""Tests for scripts/generate_chart_footprint.py.

    python3 -m unittest discover -s tests -p 'test_generate_chart_footprint.py'

The generator is the only source for the operator-rendered half of the quota preflight's
arithmetic, so a parser of its that silently returns 0 puts a green render in front of a
quota that cannot fit the release.

`tests/test_quota_preflight.py` already runs the script end to end, but through
`subprocess.run`, which exercises the behaviour and measures none of it: coverage.py in
the parent process does not instrument a child interpreter. These tests import the module
instead, so the parsers, the formatters and every exit path of `main()` are reached
directly.
"""

from __future__ import annotations

import importlib.util
import io
import pathlib
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_MODULE_PATH = REPO_ROOT / "scripts" / "generate_chart_footprint.py"
_spec = importlib.util.spec_from_file_location("generate_chart_footprint", _MODULE_PATH)
gcf = importlib.util.module_from_spec(_spec)
sys.modules["generate_chart_footprint"] = gcf
_spec.loader.exec_module(gcf)

_MIB = 1024**2
_GIB = 1024**3


def _container(name="c", requests=None, limits=None):
    """A container as it appears in the golden, with only the keys the generator reads."""
    resources = {}
    if requests is not None:
        resources["requests"] = requests
    if limits is not None:
        resources["limits"] = limits
    container = {"name": name}
    if resources:
        container["resources"] = resources
    return container


class ParseQuantityTest(unittest.TestCase):
    """The parsers. Returning 0 for something unreadable is the failure that matters."""

    def test_binary_si_suffixes(self):
        self.assertEqual(gcf.parse_bytes("1Ki"), 1024)
        self.assertEqual(gcf.parse_bytes("2Mi"), 2 * _MIB)
        self.assertEqual(gcf.parse_bytes("3Gi"), 3 * _GIB)
        self.assertEqual(gcf.parse_bytes("1Ti"), 1024**4)
        self.assertEqual(gcf.parse_bytes("1Pi"), 1024**5)
        self.assertEqual(gcf.parse_bytes("1Ei"), 1024**6)

    def test_decimal_si_suffixes(self):
        self.assertEqual(gcf.parse_bytes("1k"), 1000)
        self.assertEqual(gcf.parse_bytes("1M"), 1000**2)
        self.assertEqual(gcf.parse_bytes("1G"), 1000**3)
        self.assertEqual(gcf.parse_bytes("1T"), 1000**4)
        self.assertEqual(gcf.parse_bytes("1P"), 1000**5)
        self.assertEqual(gcf.parse_bytes("1E"), 1000**6)

    def test_bare_and_non_string_quantities(self):
        self.assertEqual(gcf.parse_bytes("512"), 512)
        self.assertEqual(gcf.parse_bytes(512), 512)
        self.assertEqual(gcf.parse_bytes(512.0), 512)
        self.assertEqual(gcf.parse_bytes(""), 0)
        self.assertEqual(gcf.parse_bytes("  "), 0)

    def test_binary_is_preferred_over_decimal(self):
        """`1Ei` must read as a binary exbibyte, not a decimal exabyte."""
        self.assertEqual(gcf.parse_bytes("1Ei"), 1024**6)
        self.assertNotEqual(gcf.parse_bytes("1Ei"), 1000**6)

    def test_cpu_millis(self):
        self.assertEqual(gcf.parse_cpu_millis("150m"), 150)
        self.assertEqual(gcf.parse_cpu_millis("1"), 1000)
        self.assertEqual(gcf.parse_cpu_millis("2.5"), 2500)
        self.assertEqual(gcf.parse_cpu_millis(2), 2000)
        self.assertEqual(gcf.parse_cpu_millis(0.5), 500)
        self.assertEqual(gcf.parse_cpu_millis(""), 0)


class FormatQuantityTest(unittest.TestCase):
    """These mirror kube-agents.formatCpu / formatBytes, so the two must agree."""

    def test_format_cpu(self):
        self.assertEqual(gcf.format_cpu(1000), "1")
        self.assertEqual(gcf.format_cpu(4500), "4500m")
        self.assertEqual(gcf.format_cpu(0), "0m")

    def test_format_bytes(self):
        self.assertEqual(gcf.format_bytes(_GIB), "1Gi")
        self.assertEqual(gcf.format_bytes(2560 * _MIB), "2560Mi")
        self.assertEqual(gcf.format_bytes(0), "0")

    def test_format_bytes_falls_back_to_a_bare_count(self):
        """Not every byte count is a whole binary unit; rounding one would be a lie."""
        self.assertEqual(gcf.format_bytes(1000000000), "1000000000")

    def test_the_two_formatters_round_trip_through_the_parsers(self):
        for value in (_GIB, 2560 * _MIB, 3 * _GIB):
            self.assertEqual(gcf.parse_bytes(gcf.format_bytes(value)), value)
        for millis in (1000, 4500, 150):
            self.assertEqual(gcf.parse_cpu_millis(gcf.format_cpu(millis)), millis)


class PrettierDumperTest(unittest.TestCase):
    """The generated file is checked by both `--check` and Prettier, which must agree."""

    def _dump(self, data):
        return yaml.dump(data, Dumper=gcf._PrettierCompatibleDumper, sort_keys=False)

    def test_numeric_looking_strings_are_double_quoted(self):
        out = self._dump({"cpu": "1", "eph": "0"})
        self.assertIn('cpu: "1"', out)
        self.assertIn('eph: "0"', out)
        self.assertNotIn("'1'", out)

    def test_unit_bearing_strings_stay_plain(self):
        out = self._dump({"cpu": "150m", "memory": "2Gi"})
        self.assertIn("cpu: 150m", out)
        self.assertIn("memory: 2Gi", out)
        self.assertNotIn('"150m"', out)


class WorkloadEntryTest(unittest.TestCase):
    def test_containers_are_summed(self):
        entry = gcf._workload_entry(
            [
                _container(requests={"cpu": "100m", "memory": "128Mi"}),
                _container(requests={"cpu": "150m", "memory": "384Mi"}),
            ],
            pods=1,
        )
        self.assertEqual(entry["cpuMillisRequest"], 250)
        self.assertEqual(entry["memoryBytesRequest"], 512 * _MIB)
        self.assertEqual(entry["requests"]["cpu"], "250m")
        self.assertEqual(entry["pods"], 1)

    def test_a_container_without_resources_contributes_zero(self):
        entry = gcf._workload_entry([_container()], pods=0)
        self.assertEqual(entry["cpuMillisLimit"], 0)
        self.assertEqual(entry["memoryBytesLimit"], 0)
        self.assertEqual(entry["ephemeralStorageBytesLimit"], 0)
        self.assertEqual(entry["limits"]["ephemeral-storage"], "0")

    def test_claim_storage_defaults_to_zero(self):
        self.assertEqual(gcf._claim_storage({}), 0)
        self.assertEqual(
            gcf._claim_storage({"spec": {"resources": {"requests": {"storage": "8Gi"}}}}),
            8 * _GIB,
        )

    def test_pod_spec_tolerates_a_document_with_no_template(self):
        self.assertEqual(gcf._pod_spec({}), {})
        self.assertEqual(
            gcf._pod_spec({"spec": {"template": {"spec": {"a": 1}}}}), {"a": 1}
        )


class ExtractFootprintTest(unittest.TestCase):
    """Runs against the committed golden, which is the generator's real input."""

    @classmethod
    def setUpClass(cls):
        cls.data = gcf.extract_footprint()
        cls.op = cls.data["operatorRendered"]

    def test_shape(self):
        for key in ("agentPod", "shellSandbox", "credentialProxy", "storage"):
            self.assertIn(key, self.op)
        self.assertIn("base", self.op["agentPod"])
        self.assertIn("dashboard", self.op["agentPod"])

    def test_the_agent_pod_base_sums_its_three_containers(self):
        """platform-agent + fluent-bit + the agent-api-auth native sidecar."""
        base = self.op["agentPod"]["base"]
        self.assertEqual(base["cpuMillisRequest"], 1000 + 100 + 150)
        self.assertEqual(base["cpuMillisLimit"], 3000 + 500 + 1000)
        self.assertEqual(base["pods"], 1)

    def test_the_dashboard_adds_resources_but_no_pod(self):
        self.assertEqual(self.op["agentPod"]["dashboard"]["pods"], 0)
        self.assertGreater(self.op["agentPod"]["dashboard"]["cpuMillisRequest"], 0)

    def test_true_init_containers_are_excluded(self):
        """A pod's request is max(largest init, sum of the rest), and the sum dominates.

        `sandbox-credential-cleanup` (100m) and `sandbox-ssh-key` (10m) are ordinary init
        containers; counting them would inflate every install's footprint. `agent-api-auth`
        carries restartPolicy: Always, so it is a sidecar and does count -- the assertion
        above covers it.
        """
        base = self.op["agentPod"]["base"]
        self.assertEqual(base["cpuMillisRequest"], 1250)
        self.assertNotEqual(base["cpuMillisRequest"], 1250 + 100 + 10)

    def test_claims_are_counted(self):
        self.assertEqual(self.op["storage"]["persistentVolumeClaims"], 4)
        self.assertEqual(self.op["storage"]["storageBytesRequest"], 22 * _GIB)

    def test_a_renamed_container_is_an_error_rather_than_a_zero(self):
        """Finding containers by name means a rename must fail loudly, not sum to 0."""
        docs = list(yaml.safe_load_all(gcf._GOLDEN_MANIFEST.read_text()))
        for doc in docs:
            if isinstance(doc, dict) and doc.get("kind") == "StatefulSet":
                for container in doc["spec"]["template"]["spec"].get("containers", []):
                    if container.get("name") == gcf._SHELL_CONTAINER:
                        container["name"] = "renamed"
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            yaml.safe_dump_all(docs, fh)
            path = pathlib.Path(fh.name)
        try:
            with unittest.mock.patch.object(gcf, "_GOLDEN_MANIFEST", path):
                with self.assertRaises(ValueError) as caught:
                    gcf.extract_footprint()
            self.assertIn(gcf._SHELL_CONTAINER, str(caught.exception))
        finally:
            path.unlink(missing_ok=True)

    def test_empty_documents_in_the_stream_are_skipped(self):
        """A stray `---` yields a None document; iterating it must not crash the sum."""
        padded = "---\n" + gcf._GOLDEN_MANIFEST.read_text() + "\n---\n"
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write(padded)
            path = pathlib.Path(fh.name)
        try:
            with unittest.mock.patch.object(gcf, "_GOLDEN_MANIFEST", path):
                padded_data = gcf.extract_footprint()
            self.assertEqual(padded_data, self.data)
        finally:
            path.unlink(missing_ok=True)

    def test_a_missing_golden_is_an_error(self):
        missing = gcf._GOLDEN_MANIFEST.parent / "does-not-exist.yaml"
        with unittest.mock.patch.object(gcf, "_GOLDEN_MANIFEST", missing):
            with self.assertRaises(FileNotFoundError):
                gcf.extract_footprint()


class MainExitCodeTest(unittest.TestCase):
    """`make chart-check` reads these apart: 1 is drift, anything else is "cannot run"."""

    def _run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with unittest.mock.patch.object(sys, "argv", argv):
            try:
                with redirect_stdout(out), redirect_stderr(err):
                    gcf.main()
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_check_passes_against_the_committed_file(self):
        code, out, _ = self._run_main(["generate_chart_footprint.py", "--check"])
        self.assertEqual(code, 0)
        self.assertIn("in sync", out)

    def test_check_reports_drift_as_exit_1(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write("operatorRendered: {}\n")
            path = pathlib.Path(fh.name)
        try:
            with unittest.mock.patch.object(gcf, "_FOOTPRINT_FILE", path):
                code, _, err = self._run_main(["generate_chart_footprint.py", "--check"])
            self.assertEqual(code, gcf._EXIT_DRIFT)
            self.assertIn("out of date", err)
        finally:
            path.unlink(missing_ok=True)

    def test_a_missing_footprint_is_drift_not_a_crash(self):
        missing = pathlib.Path(tempfile.gettempdir()) / "no-such-footprint.yaml"
        with unittest.mock.patch.object(gcf, "_FOOTPRINT_FILE", missing):
            code, _, err = self._run_main(["generate_chart_footprint.py", "--check"])
        self.assertEqual(code, gcf._EXIT_DRIFT)
        self.assertIn("does not exist", err)

    def test_an_unreadable_golden_is_exit_2_not_drift(self):
        """Reporting this as drift would send the reader to re-sync, which cannot help."""
        missing = gcf._GOLDEN_MANIFEST.parent / "does-not-exist.yaml"
        with unittest.mock.patch.object(gcf, "_GOLDEN_MANIFEST", missing):
            code, _, err = self._run_main(["generate_chart_footprint.py", "--check"])
        self.assertEqual(code, gcf._EXIT_CANNOT_RUN)
        self.assertIn("cannot build the chart footprint", err)

    def test_write_mode_reproduces_the_committed_file(self):
        committed = REPO_ROOT / "charts" / "kube-agents" / "files" / "footprint.yaml"
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "footprint.yaml"
            with unittest.mock.patch.object(gcf, "_FOOTPRINT_FILE", path):
                code, out, _ = self._run_main(["generate_chart_footprint.py"])
            self.assertEqual(code, 0)
            self.assertIn("Generated", out)
            self.assertEqual(path.read_text(), committed.read_text())

    def test_the_generated_file_carries_the_do_not_edit_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "footprint.yaml"
            with unittest.mock.patch.object(gcf, "_FOOTPRINT_FILE", path):
                self._run_main(["generate_chart_footprint.py"])
            self.assertTrue(path.read_text().startswith(gcf._HEADER))
            self.assertIn("do not edit", path.read_text())


if __name__ == "__main__":
    unittest.main()
