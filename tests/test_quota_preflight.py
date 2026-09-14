"""Tests for the chart's render-time ResourceQuota preflight (#749).

Run with the repository's runner: `make test-python`, or directly
`python3 -m unittest discover -s tests -p 'test_*.py'`. The `tests/` directory is inside
PYTHON_TEST_DIRS, so these run on every pull request.

The preflight is split into three templates so that the two that need no cluster can be
tested without one (see the comment above `kube-agents.quotaRequirements` in _helpers.tpl):

- `kube-agents.quotaRequirements` totals what the release needs;
- `kube-agents.quotaCheckItems` compares those totals against ResourceQuota objects
  handed to it;
- `kube-agents.quotaPreflight` does the cluster lookup and calls the two above.

The tests below render a throwaway copy of the chart with a probe template that calls the
first two directly, which is what lets them assert the arithmetic and the pass/fail
decision rather than only that the templates exist.
"""

import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CHART = _ROOT / "charts" / "kube-agents"
_VALUES = _CHART / "values.yaml"
_FOOTPRINT = _CHART / "files" / "footprint.yaml"
_SCHEMA = _CHART / "values.schema.json"
_PREFLIGHT_TPL = _CHART / "templates" / "quota-preflight.yaml"
_HELPERS = _CHART / "templates" / "_helpers.tpl"
_FOOTPRINT_SCRIPT = _ROOT / "scripts" / "generate_chart_footprint.py"

# helm is not on PATH in every environment; the operator's pinned copy usually is present.
_VENDORED_HELM = _ROOT / "k8s-operator" / "bin" / "helm"
_HELM = shutil.which("helm") or (str(_VENDORED_HELM) if _VENDORED_HELM.is_file() else None)

_PROBE_TEMPLATE = """
{{- if .Values.probe.emitRequirements }}
apiVersion: v1
kind: ConfigMap
metadata:
  name: probe-requirements
data:
  requirements: |
    {{ include "kube-agents.quotaRequirements" . }}
{{- end }}
{{- if .Values.probe.quotas }}
{{- include "kube-agents.quotaCheckItems" (dict
      "ctx" .
      "items" .Values.probe.quotas
      "required" (include "kube-agents.quotaRequirements" . | fromJson)) }}
{{- end }}
"""

# Totals for a stock install, re-derived by hand from values.yaml and footprint.yaml. These
# are the same numbers the install prerequisites table quotes, so a change that moves one
# without updating the other fails here.
_DEFAULT_PODS = 6
_DEFAULT_REQUESTS_CPU_MILLIS = 2016
_DEFAULT_LIMITS_CPU_MILLIS = 10000
# Memory and ephemeral storage are summed by the same helper as CPU but were asserted
# nowhere, so a generator that stopped parsing them could be regenerated and committed
# together with a green `--check` (both sides move at once) and nothing would catch it.
_DEFAULT_REQUESTS_MEMORY_BYTES = 4928 * 1024**2
_DEFAULT_LIMITS_MEMORY_BYTES = 19840 * 1024**2
_DEFAULT_REQUESTS_EPHEMERAL_BYTES = 1 * 1024**3
_DEFAULT_LIMITS_EPHEMERAL_BYTES = 5 * 1024**3
# Dashboard container: 256m requested, 1 core limit, inside the agent pod.
_DASHBOARD_REQUEST_MILLIS = 256
_DASHBOARD_LIMIT_MILLIS = 1000
# One agent pod (base + dashboard) as requested.
_AGENT_POD_REQUEST_MILLIS = 1506
# The operator's own request.
_OPERATOR_REQUEST_MILLIS = 10
# Two operator-rendered claims plus two from the shell StatefulSet's volumeClaimTemplates.
_OPERATOR_PVC_COUNT = 4
_OPERATOR_STORAGE_BYTES = 22 * 1024**3

_REQUIRED_HARNESS = [
    "--set",
    "platformAgent.harness.clusterName=c",
    "--set",
    "platformAgent.harness.location=us-east4",
    "--set",
    "platformAgent.harness.projectId=p",
]


@unittest.skipUnless(_HELM, "helm is not installed")
class PreflightDecisionTest(unittest.TestCase):
    """Drives the preflight's arithmetic and its pass/fail decision without a cluster."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.chart = pathlib.Path(cls._tmp.name) / "kube-agents"
        shutil.copytree(_CHART, cls.chart)
        # The probe adds a value the shipped schema does not declare, and the schema is
        # closed. Dropping it from the throwaway copy keeps the probe out of the real chart.
        (cls.chart / "values.schema.json").unlink(missing_ok=True)
        (cls.chart / "templates" / "zz-probe.yaml").write_text(_PROBE_TEMPLATE)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _render(self, values: dict, sets: list[str] | None = None):
        """Render the probe chart. Returns the CompletedProcess without asserting on it."""
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            yaml.safe_dump(values, fh)
            values_path = fh.name
        args = [_HELM, "template", "test-release", str(self.chart), "-f", values_path]
        args += _REQUIRED_HARNESS
        for item in sets or []:
            args += ["--set", item]
        try:
            return subprocess.run(args, capture_output=True, text=True)
        finally:
            pathlib.Path(values_path).unlink(missing_ok=True)

    def _requirements(self, sets: list[str] | None = None) -> dict:
        res = self._render({"probe": {"emitRequirements": True}}, sets)
        self.assertEqual(res.returncode, 0, f"render failed:\n{res.stderr}")
        for doc in yaml.safe_load_all(res.stdout):
            if doc and doc.get("metadata", {}).get("name") == "probe-requirements":
                return json.loads(doc["data"]["requirements"])
        self.fail(f"probe ConfigMap not found in output:\n{res.stdout}")

    def test_default_totals_match_the_documented_footprint(self) -> None:
        req = self._requirements()
        self.assertEqual(req["pods"], _DEFAULT_PODS)
        self.assertEqual(req["requestsCpu"], _DEFAULT_REQUESTS_CPU_MILLIS)
        self.assertEqual(req["limitsCpu"], _DEFAULT_LIMITS_CPU_MILLIS)
        self.assertEqual(req["requestsMemory"], _DEFAULT_REQUESTS_MEMORY_BYTES)
        self.assertEqual(req["limitsMemory"], _DEFAULT_LIMITS_MEMORY_BYTES)
        self.assertEqual(req["requestsEphemeral"], _DEFAULT_REQUESTS_EPHEMERAL_BYTES)
        self.assertEqual(req["limitsEphemeral"], _DEFAULT_LIMITS_EPHEMERAL_BYTES)

    def test_disabling_the_dashboard_drops_it_from_the_total(self) -> None:
        """The flag is harness.hermes.dashboardEnabled, one level deeper than harness.

        Read at harness.dashboardEnabled the lookup matches nothing, the branch never runs,
        and a dashboard-off install is charged for a dashboard it will not schedule.
        """
        base = self._requirements()
        off = self._requirements(["platformAgent.harness.hermes.dashboardEnabled=false"])
        self.assertEqual(
            base["requestsCpu"] - off["requestsCpu"], _DASHBOARD_REQUEST_MILLIS
        )
        self.assertEqual(base["limitsCpu"] - off["limitsCpu"], _DASHBOARD_LIMIT_MILLIS)
        # The dashboard shares the agent's pod, so switching it off frees no pod.
        self.assertEqual(base["pods"], off["pods"])

    def test_agent_replicas_multiply_the_agent_pod(self) -> None:
        """An HA install must not pass a check sized for one replica."""
        replicas = 3
        base = self._requirements()
        ha = self._requirements(
            [f"platformAgent.deployment.availability.replicas={replicas}"]
        )
        self.assertEqual(ha["pods"] - base["pods"], replicas - 1)
        self.assertEqual(
            ha["requestsCpu"] - base["requestsCpu"],
            _AGENT_POD_REQUEST_MILLIS * (replicas - 1),
        )

    def test_operator_replicas_are_counted(self) -> None:
        replicas = 3
        base = self._requirements()
        scaled = self._requirements([f"operator.replicaCount={replicas}"])
        self.assertEqual(scaled["pods"] - base["pods"], replicas - 1)
        self.assertEqual(
            scaled["requestsCpu"] - base["requestsCpu"],
            _OPERATOR_REQUEST_MILLIS * (replicas - 1),
        )

    def test_claims_are_counted_and_do_not_scale_with_replicas(self) -> None:
        base = self._requirements()
        self.assertEqual(base["persistentVolumeClaims"], _OPERATOR_PVC_COUNT)
        self.assertEqual(base["requestsStorage"], _OPERATOR_STORAGE_BYTES)
        ha = self._requirements(["platformAgent.deployment.availability.replicas=3"])
        self.assertEqual(ha["persistentVolumeClaims"], _OPERATOR_PVC_COUNT)

    def _quota(self, hard: dict, used: dict | None = None, **extra) -> dict:
        spec = {"hard": hard}
        spec.update(extra)
        return {
            "metadata": {"name": "test-quota"},
            "spec": spec,
            "status": {"used": used or {}},
        }

    def test_a_quota_that_cannot_fit_the_release_fails_the_render(self) -> None:
        res = self._render({"probe": {"quotas": [self._quota({"pods": "2"})]}})
        self.assertNotEqual(res.returncode, 0, "render should have failed")
        self.assertIn("test-quota", res.stderr)
        self.assertIn("pods", res.stderr)
        self.assertIn("kubectl patch resourcequota", res.stderr)

    def test_a_quota_with_room_passes(self) -> None:
        res = self._render(
            {
                "probe": {
                    "quotas": [
                        self._quota(
                            {"pods": "50", "requests.cpu": "100", "limits.cpu": "200"}
                        )
                    ]
                }
            }
        )
        self.assertEqual(res.returncode, 0, f"render should have passed:\n{res.stderr}")

    def test_a_scoped_quota_is_skipped(self) -> None:
        """A scoped quota covers a subset of pods this template cannot identify."""
        res = self._render(
            {
                "probe": {
                    "quotas": [
                        self._quota({"pods": "1"}, scopes=["BestEffort"]),
                        self._quota(
                            {"pods": "1"},
                            scopeSelector={"matchExpressions": []},
                        ),
                    ]
                }
            }
        )
        self.assertEqual(res.returncode, 0, f"scoped quotas must be skipped:\n{res.stderr}")

    def test_the_patch_leaves_room_for_a_surge_pod(self) -> None:
        """used + required exactly fits at rest and then stalls the first rollout."""
        res = self._render({"probe": {"quotas": [self._quota({"pods": "2"})]}})
        self.assertNotEqual(res.returncode, 0)
        # Required is the default pod count; the patch must ask for more than that.
        self.assertIn(f'"pods":"{_DEFAULT_PODS + 1}"', res.stderr)

    def test_large_binary_units_parse(self) -> None:
        """`1Pi` must not read as 0 and refuse the release against a huge quota."""
        res = self._render(
            {
                "probe": {
                    "quotas": [
                        self._quota({"limits.memory": "1Pi", "requests.storage": "1Pi"})
                    ]
                }
            }
        )
        self.assertEqual(res.returncode, 0, f"1Pi should be ample:\n{res.stderr}")

    def test_an_unparseable_quantity_fails_loudly(self) -> None:
        """Falling through to 0 would refuse the release and misdescribe the cluster."""
        res = self._render({"probe": {"quotas": [self._quota({"limits.memory": "12xyz"})]}})
        self.assertNotEqual(res.returncode, 0, "an unreadable quantity must not be guessed")
        self.assertIn("cannot parse", res.stderr)

    def test_unmodelled_quota_keys_are_ignored(self) -> None:
        res = self._render(
            {"probe": {"quotas": [self._quota({"services": "1", "secrets": "1"})]}}
        )
        self.assertEqual(
            res.returncode, 0, f"unmodelled keys must be skipped:\n{res.stderr}"
        )


class QuotaPreflightTest(unittest.TestCase):
    def test_footprint_file_structure(self) -> None:
        self.assertTrue(_FOOTPRINT.is_file(), f"missing {_FOOTPRINT}")
        data = yaml.safe_load(_FOOTPRINT.read_text())
        self.assertIn("operatorRendered", data)
        op = data["operatorRendered"]
        self.assertIn("agentPod", op)
        self.assertIn("base", op["agentPod"])
        self.assertIn("dashboard", op["agentPod"])
        self.assertIn("shellSandbox", op)
        self.assertIn("credentialProxy", op)
        self.assertIn("storage", op)

        # Verify agent-api-auth CPU limit cut to 1 core is reflected in footprint
        # Base: platform-agent (3 CPU limit) + fluent-bit (0.5 CPU limit) + agent-api-auth (1 CPU limit) = 4.5 CPU
        self.assertEqual(op["agentPod"]["base"]["cpuMillisLimit"], 4500)
        self.assertEqual(op["agentPod"]["dashboard"]["cpuMillisLimit"], 1000)
        self.assertEqual(op["shellSandbox"]["cpuMillisLimit"], 2000)
        self.assertEqual(op["credentialProxy"]["cpuMillisLimit"], 1000)

        # Total operator limits on default install: 4500 + 1000 + 2000 + 1000 = 8500m (8.5 CPU)
        total_op_cpu_limit = (
            op["agentPod"]["base"]["cpuMillisLimit"]
            + op["agentPod"]["dashboard"]["cpuMillisLimit"]
            + op["shellSandbox"]["cpuMillisLimit"]
            + op["credentialProxy"]["cpuMillisLimit"]
        )
        self.assertEqual(total_op_cpu_limit, 8500)

        # Memory, in the same shape. A generator that stopped parsing memory would emit 0
        # here; `--check` alone would not catch that if the regenerated file were committed
        # in the same change, because both sides would move together.
        self.assertEqual(op["agentPod"]["base"]["memoryBytesLimit"], 10496 * 1024**2)
        self.assertEqual(op["agentPod"]["dashboard"]["memoryBytesLimit"], 2 * 1024**3)
        self.assertEqual(op["shellSandbox"]["memoryBytesLimit"], 2 * 1024**3)
        self.assertEqual(op["credentialProxy"]["memoryBytesLimit"], 1 * 1024**3)

        # Ephemeral storage is set on two of the four and deliberately 0 on the others;
        # asserting the zeroes is the point, since an unparsed value looks identical.
        self.assertEqual(op["agentPod"]["base"]["ephemeralStorageBytesLimit"], 3 * 1024**3)
        self.assertEqual(op["agentPod"]["dashboard"]["ephemeralStorageBytesLimit"], 0)
        self.assertEqual(op["shellSandbox"]["ephemeralStorageBytesLimit"], 0)
        self.assertEqual(
            op["credentialProxy"]["ephemeralStorageBytesLimit"], 2 * 1024**3
        )

        self.assertEqual(op["storage"]["persistentVolumeClaims"], _OPERATOR_PVC_COUNT)
        self.assertEqual(op["storage"]["storageBytesRequest"], _OPERATOR_STORAGE_BYTES)

    def test_footprint_sync_and_check(self) -> None:
        """Verify footprint generator script runs clean in --check mode."""
        self.assertTrue(_FOOTPRINT_SCRIPT.is_file(), f"missing {_FOOTPRINT_SCRIPT}")
        res = subprocess.run(
            ["python3", str(_FOOTPRINT_SCRIPT), "--check"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            res.returncode,
            0,
            f"footprint drift detected:\n{res.stderr}\n{res.stdout}",
        )

    def test_values_yaml_quota_preflight_enabled(self) -> None:
        values = yaml.safe_load(_VALUES.read_text())
        self.assertIn("quotaPreflight", values)
        self.assertTrue(values["quotaPreflight"].get("enabled"))

    def test_quota_preflight_is_declared_in_the_values_schema(self) -> None:
        """The schema is closed, so an undeclared key fails every helm command."""
        schema = json.loads(_SCHEMA.read_text())
        self.assertIn("quotaPreflight", schema["properties"])
        self.assertIn("enabled", schema["properties"]["quotaPreflight"]["properties"])

    def test_templates_exist(self) -> None:
        self.assertTrue(_PREFLIGHT_TPL.is_file(), f"missing {_PREFLIGHT_TPL}")
        tpl_content = _PREFLIGHT_TPL.read_text()
        self.assertIn("kube-agents.quotaPreflight", tpl_content)

        helpers_content = _HELPERS.read_text()
        for name in (
            "kube-agents.quotaPreflight",
            "kube-agents.quotaRequirements",
            "kube-agents.quotaCheckItems",
            "kube-agents.parseCpuMillis",
            "kube-agents.parseBytes",
            "kube-agents.formatCpu",
            "kube-agents.formatBytes",
        ):
            self.assertIn(f'define "{name}"', helpers_content)

    @unittest.skipUnless(_HELM, "helm is not installed")
    def test_helm_template_inert_without_cluster(self) -> None:
        res = subprocess.run(
            [
                _HELM,
                "template",
                "test-release",
                str(_CHART),
                "--set",
                "quotaPreflight.enabled=true",
                *_REQUIRED_HARNESS,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"helm template failed:\n{res.stderr}")


if __name__ == "__main__":
    unittest.main()
