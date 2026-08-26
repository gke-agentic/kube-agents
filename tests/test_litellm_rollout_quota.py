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
import unittest

import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_VALUES = _ROOT / "charts" / "kube-agents" / "values.yaml"
_CHART_TEMPLATE = _ROOT / "charts" / "kube-agents" / "templates" / "litellm.yaml"
_KUSTOMIZE_BASE = (
    _ROOT / "k8s-operator" / "config" / "integrations" / "litellm" / "base" / "deployment.yaml"
)
_EXAMPLE = _ROOT / "examples" / "litellm-gemini" / "deployment.yaml"


def _is_litellm_deployment(path):
    """True for a complete, standalone LiteLLM Deployment manifest.

    Two things this deliberately excludes. A kustomize strategic-merge patch
    declares `kind: Deployment` but carries no `spec.selector`, which a real
    Deployment must have — it inherits the base's strategy and is not a copy of
    it. And a workload that merely talks to LiteLLM (examples/inference-replay)
    mentions the name without being one, so identity is taken from the
    Deployment's own name or its container's, not from the file's text.
    """
    for doc in yaml.safe_load_all(path.read_text()):
        if not doc or doc.get("kind") != "Deployment":
            continue
        spec = doc.get("spec") or {}
        if "selector" not in spec:
            continue
        containers = (spec.get("template", {}).get("spec", {}) or {}).get("containers") or []
        names = {doc.get("metadata", {}).get("name")} | {c.get("name") for c in containers}
        if "litellm" in names or "litellm-container" in names:
            return True
    return False


def _max_unavailable(path):
    """Return the resolved maxUnavailable of the Deployment in a plain YAML file.

    An absent `strategy` block is not a pass. Kubernetes defaults it to
    RollingUpdate at 25%/25%, and a maxUnavailable percentage rounds DOWN — so
    an omitted strategy resolves to 0 at 1 to 3 replicas, which is the very
    shape this suite exists to keep out. Report it as 0 rather than raising.
    """
    for doc in yaml.safe_load_all(path.read_text()):
        if not doc or doc.get("kind") != "Deployment":
            continue
        strategy = doc["spec"].get("strategy") or {}
        if strategy.get("type") == "Recreate":
            # Recreate takes no surge Pod at all, so it cannot hit #749.
            return None
        rolling = strategy.get("rollingUpdate")
        if not rolling or "maxUnavailable" not in rolling:
            return 0
        value = rolling["maxUnavailable"]
        if isinstance(value, str) and value.endswith("%"):
            # floor(pct * replicas), Kubernetes' rounding for this fencepost.
            replicas = doc["spec"].get("replicas", 1)
            return int(int(value[:-1]) * replicas / 100)
        return int(value)
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
        # The default above is only load-bearing if the template wires it
        # through. Rendering with helm would test the property directly, but
        # nothing else in tests/ shells out to helm and the python-tests job
        # does not install it, so this matches the template text instead —
        # loosely enough that a pipeline (`| int`, `| default 1`) or extra
        # whitespace, neither of which changes what renders, does not fail it.
        template = _CHART_TEMPLATE.read_text()
        self.assertRegex(
            template,
            r"maxUnavailable:\s*\{\{[^}]*\.Values\.litellm\.rollingUpdate\.maxUnavailable[^}]*\}\}",
            "charts/kube-agents/templates/litellm.yaml must render maxUnavailable from "
            "litellm.rollingUpdate, so an install can choose its own value",
        )
        self.assertNotRegex(
            template,
            r"maxUnavailable:\s*\d",
            "charts/kube-agents/templates/litellm.yaml must not hardcode maxUnavailable; "
            "a literal would pin every install regardless of its quota",
        )

    def test_kustomize_base_replaces_in_place(self):
        self.assertGreaterEqual(
            _max_unavailable(_KUSTOMIZE_BASE),
            1,
            f"{_KUSTOMIZE_BASE.relative_to(_ROOT)} is kept in step with the chart "
            "template per AGENTS.md; maxUnavailable must be at least 1 (#749)",
        )

    def test_starting_template_replaces_in_place(self):
        self.assertGreaterEqual(
            _max_unavailable(_EXAMPLE),
            1,
            f"{_EXAMPLE.relative_to(_ROOT)} is linked from the docs site as a starting "
            "template, so it carries the same requirement (#749)",
        )

    def test_no_other_litellm_deployment_reintroduces_the_shape(self):
        # A fourth copy added later would not be caught by the three cases
        # above. Sweep every plain LiteLLM Deployment manifest in the tree.
        #
        # Omitting `strategy` is a way to reintroduce this, not a way to avoid
        # it: Kubernetes defaults to RollingUpdate at 25%/25% and rounds the
        # maxUnavailable percentage down, so an absent block resolves to 0 at
        # 1 to 3 replicas. _max_unavailable reports that as 0 rather than
        # treating a missing block as unset, which is why this sweeps resolved
        # values instead of grepping for a literal.
        offenders = []
        roots = [
            _ROOT / "k8s-operator" / "config" / "integrations" / "litellm",
            _ROOT / "examples",
        ]
        for root in roots:
            for path in sorted(root.rglob("*.yaml")):
                if not _is_litellm_deployment(path):
                    continue
                resolved = _max_unavailable(path)
                # None is Recreate: no surge Pod at all, immune by construction.
                if resolved is not None and resolved < 1:
                    offenders.append(f"{path.relative_to(_ROOT)} (resolves to {resolved})")
        self.assertEqual(
            [],
            offenders,
            "these LiteLLM Deployments resolve maxUnavailable to 0, so they cannot roll "
            "under a full namespace quota (#749). An absent strategy block counts: it "
            "defaults to 25%, which rounds down to 0 at these replica counts.",
        )


if __name__ == "__main__":
    unittest.main()
