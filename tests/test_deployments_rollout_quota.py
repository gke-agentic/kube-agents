"""Tests that all Deployments can finish a rollout under a full namespace quota (#975).

    python3 -m unittest discover -s tests -p 'test_*.py'

Stdlib unittest, no pytest, matching the other suites in this directory.

Deployments with `replicas: 1` and no explicit `strategy` block default to
`RollingUpdate` with `maxSurge: 25%` (ceil = 1) and `maxUnavailable: 25%` (floor = 0).
Where a namespace `ResourceQuota` has no room for one more Pod, the surge Pod is
refused with `FailedCreate` and the rollout stalls indefinitely because the old
Pod cannot be scaled down (`maxUnavailable: 0`).

`maxUnavailable` of at least 1 (or `strategy.type: Recreate`) allows the rollout
to replace pods in place under a full quota.

This suite asserts that all Deployments shipped by the repository define an
explicit rollout strategy meeting this requirement (#975).
"""

import math
import pathlib
import re
import unittest

import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[1]

_OPERATOR_CHART_TEMPLATE = (
    _ROOT / "charts" / "kube-agents" / "templates" / "operator-deployment.yaml"
)
_OPERATOR_KUSTOMIZE = _ROOT / "k8s-operator" / "config" / "manager" / "manager.yaml"

_HINDSIGHT_CHART_TEMPLATE = (
    _ROOT / "charts" / "kube-agents" / "templates" / "hindsight.yaml"
)
_HINDSIGHT_KUSTOMIZE = (
    _ROOT / "k8s-operator" / "config" / "integrations" / "hindsight" / "api.yaml"
)

_REPLAY_KUSTOMIZE = (
    _ROOT
    / "k8s-operator"
    / "config"
    / "integrations"
    / "inference-replay"
    / "base"
    / "deployment.yaml"
)
_REPLAY_EXAMPLE = _ROOT / "examples" / "inference-replay" / "deployment.yaml"

_VLLM_GEMMA_EXAMPLE = _ROOT / "examples" / "vllm-gemma" / "deployment.yaml"
_GITHUB_MINTER_TEMPLATE = (
    _ROOT / "charts" / "kube-agents" / "templates" / "github-minter.yaml"
)

_SCAN_ROOTS = [
    _ROOT / "k8s-operator" / "config",
    _ROOT / "examples",
]


def _extract_deployments(path):
    """Yield all Deployment documents found in a plain YAML file."""
    for doc in yaml.safe_load_all(path.read_text()):
        if not isinstance(doc, dict) or doc.get("kind") != "Deployment":
            continue
        spec = doc.get("spec")
        if not isinstance(spec, dict) or "selector" not in spec:
            continue
        yield doc


def _resolve_max_unavailable(doc):
    """Resolve a Deployment's maxUnavailable the way Kubernetes does.

    None means Recreate: no surge Pod at all, so it cannot stall on quota.
    """
    spec = doc["spec"]
    replicas = spec.get("replicas", 1)
    strategy = spec.get("strategy") or {}
    if strategy.get("type") == "Recreate":
        return None
    rolling = strategy.get("rollingUpdate") or {}

    def scaled(value, default, round_up):
        if value is None:
            value = default
        if isinstance(value, str) and value.endswith("%"):
            exact = int(value[:-1]) * replicas / 100
            return math.ceil(exact) if round_up else math.floor(exact)
        return int(value)

    surge = scaled(rolling.get("maxSurge"), "25%", True)
    unavailable = scaled(rolling.get("maxUnavailable"), "25%", False)
    if surge == 0 and unavailable == 0:
        return 1
    return unavailable


def _has_chart_rolling_update_strategy(template_text):
    """Verify that a Helm chart template contains a rollingUpdate strategy block with maxUnavailable >= 1."""
    has_strategy = "strategy:" in template_text
    has_rolling = (
        "type: RollingUpdate" in template_text or "rollingUpdate:" in template_text
    )
    has_max_unavail = (
        re.search(
            r"maxUnavailable:\s*([1-9]\d*|\{\{.+?\}\})", template_text
        )
        is not None
    )
    return has_strategy and has_rolling and has_max_unavail


class DeploymentsRolloutSurvivesAFullQuota(unittest.TestCase):
    def test_operator_chart_template_sets_rollout_strategy(self):
        text = _OPERATOR_CHART_TEMPLATE.read_text()
        self.assertTrue(
            _has_chart_rolling_update_strategy(text),
            f"{_OPERATOR_CHART_TEMPLATE.relative_to(_ROOT)} must define a RollingUpdate "
            "strategy with maxUnavailable >= 1 to allow replacing pods under full quota",
        )

    def test_operator_kustomize_sets_rollout_strategy(self):
        docs = list(_extract_deployments(_OPERATOR_KUSTOMIZE))
        self.assertEqual(len(docs), 1, f"expected 1 Deployment in {_OPERATOR_KUSTOMIZE}")
        max_unavail = _resolve_max_unavailable(docs[0])
        self.assertIsNotNone(max_unavail)
        self.assertGreaterEqual(
            max_unavail,
            1,
            f"{_OPERATOR_KUSTOMIZE.relative_to(_ROOT)} must resolve maxUnavailable >= 1",
        )

    def test_hindsight_chart_template_sets_rollout_strategy(self):
        text = _HINDSIGHT_CHART_TEMPLATE.read_text()
        self.assertTrue(
            _has_chart_rolling_update_strategy(text),
            f"{_HINDSIGHT_CHART_TEMPLATE.relative_to(_ROOT)} must define a RollingUpdate "
            "strategy with maxUnavailable >= 1 to allow replacing pods under full quota",
        )

    def test_hindsight_kustomize_sets_rollout_strategy(self):
        docs = list(_extract_deployments(_HINDSIGHT_KUSTOMIZE))
        self.assertEqual(len(docs), 1, f"expected 1 Deployment in {_HINDSIGHT_KUSTOMIZE}")
        max_unavail = _resolve_max_unavailable(docs[0])
        self.assertIsNotNone(max_unavail)
        self.assertGreaterEqual(
            max_unavail,
            1,
            f"{_HINDSIGHT_KUSTOMIZE.relative_to(_ROOT)} must resolve maxUnavailable >= 1",
        )

    def test_replay_kustomize_sets_rollout_strategy(self):
        docs = list(_extract_deployments(_REPLAY_KUSTOMIZE))
        self.assertEqual(len(docs), 1, f"expected 1 Deployment in {_REPLAY_KUSTOMIZE}")
        max_unavail = _resolve_max_unavailable(docs[0])
        self.assertIsNotNone(max_unavail)
        self.assertGreaterEqual(
            max_unavail,
            1,
            f"{_REPLAY_KUSTOMIZE.relative_to(_ROOT)} must resolve maxUnavailable >= 1",
        )

    def test_replay_example_sets_rollout_strategy(self):
        docs = list(_extract_deployments(_REPLAY_EXAMPLE))
        self.assertEqual(len(docs), 1, f"expected 1 Deployment in {_REPLAY_EXAMPLE}")
        max_unavail = _resolve_max_unavailable(docs[0])
        self.assertIsNotNone(max_unavail)
        self.assertGreaterEqual(
            max_unavail,
            1,
            f"{_REPLAY_EXAMPLE.relative_to(_ROOT)} must resolve maxUnavailable >= 1",
        )

    def test_vllm_gemma_example_sets_rollout_strategy(self):
        docs = list(_extract_deployments(_VLLM_GEMMA_EXAMPLE))
        self.assertEqual(
            len(docs), 1, f"expected 1 Deployment in {_VLLM_GEMMA_EXAMPLE}"
        )
        max_unavail = _resolve_max_unavailable(docs[0])
        self.assertIsNotNone(max_unavail)
        self.assertGreaterEqual(
            max_unavail,
            1,
            f"{_VLLM_GEMMA_EXAMPLE.relative_to(_ROOT)} must resolve maxUnavailable >= 1",
        )

    def test_github_minter_chart_template_sets_rollout_strategy(self):
        text = _GITHUB_MINTER_TEMPLATE.read_text()
        self.assertTrue(
            _has_chart_rolling_update_strategy(text),
            f"{_GITHUB_MINTER_TEMPLATE.relative_to(_ROOT)} must define a RollingUpdate "
            "strategy with maxUnavailable >= 1 to allow replacing pods under full quota",
        )

    def test_no_deployment_manifest_resolves_zero_max_unavailable(self):
        offenders = []
        for root in _SCAN_ROOTS:
            for path in sorted(root.rglob("*.yaml")):
                for doc in _extract_deployments(path):
                    resolved = _resolve_max_unavailable(doc)
                    if resolved is not None and resolved < 1:
                        name = (doc.get("metadata") or {}).get("name")
                        offenders.append(
                            f"{path.relative_to(_ROOT)} (deployment: {name}, resolves to {resolved})"
                        )
        self.assertEqual(
            [],
            offenders,
            "these Deployments resolve maxUnavailable to 0, so they cannot roll "
            "under a full namespace quota. An absent strategy block counts: it "
            "defaults to 25%, which rounds down to 0 at 1-3 replicas.",
        )


if __name__ == "__main__":
    unittest.main()
