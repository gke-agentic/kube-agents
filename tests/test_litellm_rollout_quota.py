"""Tests that a LiteLLM rollout can finish under a full namespace quota (#749).

    python3 -m unittest discover -s tests -p 'test_*.py'

Stdlib unittest, no pytest, matching the other suites in this directory.

`maxUnavailable: 0` on a Deployment makes the surge Pod mandatory: the old
ReplicaSet may not shrink until the new Pod is available, so where a namespace
`ResourceQuota` has no room for one more Pod the surge Pod is refused with
`FailedCreate` and the rollout never completes. It does not degrade — it stops,
until `progressDeadlineSeconds`, including the rollout carrying whatever fix
prompted it.

Some organizations apply a baseline quota to every namespace as policy, and
nothing in this repository creates or sizes that quota, so the harness cannot
assume headroom exists. `maxUnavailable` of at least 1 is what lets the rollout
fall back to replacing in place.

LiteLLM's Deployment exists in three files, and the value has to hold in all of
them. Only two are the pair `AGENTS.md` keeps in step on purpose; the third is a
starting template users copy, reached from four pages of the docs site:

    charts/kube-agents/templates/litellm.yaml   (via values.yaml, configurable)
    k8s-operator/config/integrations/litellm/base/deployment.yaml  (dev path)
    examples/litellm-gemini/deployment.yaml     (starting template)

Nothing else asserts this. `make chart-check` compares only the CRD and RBAC
copies, and `test_gateway_rollout_budgets.py` reads the CI rollout gate rather
than the Deployment's strategy — so before this suite the fix could be reverted
in any one of the three with every gate still green.

The gateway Deployment reaches the same trap through a percentage that rounds
down; that side is the operator's and is covered by the Go table test in
`k8s-operator/internal/controller/manifest_helpers_test.go`.
"""

import pathlib
import re
import unittest

import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_VALUES = _ROOT / "charts" / "kube-agents" / "values.yaml"
_CHART_TEMPLATE = _ROOT / "charts" / "kube-agents" / "templates" / "litellm.yaml"
_KUSTOMIZE_BASE = (
    _ROOT / "k8s-operator" / "config" / "integrations" / "litellm" / "base" / "deployment.yaml"
)
_EXAMPLE = _ROOT / "examples" / "litellm-gemini" / "deployment.yaml"


def _rolling_update(path):
    """Return the rollingUpdate block of the Deployment in a plain YAML file."""
    for doc in yaml.safe_load_all(path.read_text()):
        if doc and doc.get("kind") == "Deployment":
            return doc["spec"]["strategy"]["rollingUpdate"]
    raise AssertionError(f"no Deployment in {path}")


class LiteLLMRolloutSurvivesAFullQuota(unittest.TestCase):
    def test_chart_default_replaces_in_place(self):
        values = yaml.safe_load(_VALUES.read_text())
        self.assertGreaterEqual(
            values["litellm"]["rollingUpdate"]["maxUnavailable"],
            1,
            "charts/kube-agents/values.yaml: litellm.rollingUpdate.maxUnavailable must "
            "default to at least 1, or a rollout stalls under a full namespace quota (#749)",
        )

    def test_chart_template_reads_the_value_rather_than_hardcoding_it(self):
        # The default above is only load-bearing if the template actually wires
        # it through. A literal here would silently pin every install.
        template = _CHART_TEMPLATE.read_text()
        self.assertRegex(
            template,
            r"maxUnavailable:\s*\{\{\s*\.Values\.litellm\.rollingUpdate\.maxUnavailable\s*\}\}",
            "charts/kube-agents/templates/litellm.yaml must render maxUnavailable from "
            "values, so an install with quota headroom can choose 0",
        )
        self.assertNotRegex(
            template,
            r"maxUnavailable:\s*0\b",
            "charts/kube-agents/templates/litellm.yaml must not hardcode maxUnavailable: 0",
        )

    def test_kustomize_base_replaces_in_place(self):
        self.assertGreaterEqual(
            _rolling_update(_KUSTOMIZE_BASE)["maxUnavailable"],
            1,
            f"{_KUSTOMIZE_BASE.relative_to(_ROOT)} is kept in step with the chart "
            "template per AGENTS.md; maxUnavailable must be at least 1 (#749)",
        )

    def test_starting_template_replaces_in_place(self):
        self.assertGreaterEqual(
            _rolling_update(_EXAMPLE)["maxUnavailable"],
            1,
            f"{_EXAMPLE.relative_to(_ROOT)} is linked from the docs site as a starting "
            "template, so it carries the same requirement (#749)",
        )

    def test_no_other_litellm_deployment_reintroduces_the_shape(self):
        # A fourth copy added later would not be caught by the three cases
        # above. Sweep the directories LiteLLM manifests live in.
        offenders = []
        roots = [
            _ROOT / "charts" / "kube-agents" / "templates",
            _ROOT / "k8s-operator" / "config" / "integrations" / "litellm",
            _ROOT / "examples",
        ]
        for root in roots:
            for path in root.rglob("*.yaml"):
                text = path.read_text()
                if "litellm" not in text.lower():
                    continue
                # Recreate takes no rollingUpdate block and is immune by
                # construction, so only flag a RollingUpdate that pins 0.
                if re.search(r"maxUnavailable:\s*0\b", text):
                    offenders.append(str(path.relative_to(_ROOT)))
        self.assertEqual(
            [],
            offenders,
            "these LiteLLM manifests pin maxUnavailable: 0, which cannot roll under a "
            "full namespace quota (#749)",
        )


if __name__ == "__main__":
    unittest.main()
