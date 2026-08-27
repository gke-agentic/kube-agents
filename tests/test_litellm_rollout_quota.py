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

LiteLLM's Deployment exists in four files, and the value has to hold in the
three that roll. Only two are the pair `AGENTS.md` keeps in step on purpose:

    charts/kube-agents/templates/litellm.yaml   (via values.yaml, configurable)
    k8s-operator/config/integrations/litellm/base/deployment.yaml  (dev path)
    examples/litellm-gemini/deployment.yaml     (starting template, reached
                                                 from four docs-site pages)
    examples/litellm-chatgpt-subscription/deployment.yaml  (Recreate, so it
                                                 takes no surge Pod and cannot
                                                 hit this at all — exempt by
                                                 construction, not by omission)

Nothing else asserts this. `make chart-check` compares only the CRD and RBAC
copies, and `test_gateway_rollout_budgets.py` reads the CI rollout gate rather
than the Deployment's strategy — so before this suite the fix could be reverted
in any one of them with every gate still green.

The gateway Deployment reaches the same trap through a percentage that rounds
down; that side is the operator's and is covered by the Go table test in
`k8s-operator/internal/controller/manifest_helpers_test.go`.

Scope this suite does NOT cover, deliberately: every other Deployment the
install ships omits `strategy` entirely and runs at one replica, which resolves
to the same maxUnavailable 0 and stalls the same way — the operator's own
controller-manager among them. That is a wider change than this one and is
tracked in #975; do not read a green run here as the install being clear of it.
"""

import math
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


def _litellm_deployment(path):
    """The LiteLLM Deployment document in a plain YAML file, or None.

    Identity, not text: a workload that merely talks to LiteLLM
    (examples/inference-replay) names it without being one, so this matches on
    the Deployment's own name or its container's. A kustomize strategic-merge
    patch is excluded too — it declares `kind: Deployment` but carries no
    `spec.selector`, which a real Deployment must have, and it inherits the
    base's strategy rather than restating it.

    Returning the document rather than a bool matters: resolving the strategy
    from a *separately* located document would let a multi-document file report
    one Deployment's identity and another's strategy.
    """
    for doc in yaml.safe_load_all(path.read_text()):
        if not isinstance(doc, dict) or doc.get("kind") != "Deployment":
            continue
        spec = doc.get("spec")
        if not isinstance(spec, dict) or "selector" not in spec:
            continue
        template_spec = (spec.get("template") or {}).get("spec") or {}
        containers = template_spec.get("containers") or []
        names = {(doc.get("metadata") or {}).get("name")}
        names |= {c.get("name") for c in containers if isinstance(c, dict)}
        if names & {"litellm", "litellm-container"}:
            return doc
    return None


def _resolve_max_unavailable(doc):
    """Resolve a Deployment's maxUnavailable the way Kubernetes does.

    None means Recreate: no surge Pod at all, so it cannot hit #749.

    An absent `strategy` block is not a pass. Kubernetes defaults it to
    RollingUpdate at 25%/25%, and a maxUnavailable percentage rounds DOWN — so
    an omitted strategy resolves to 0 at 1 to 3 replicas, which is the very
    shape this suite exists to keep out.

    ResolveFenceposts' both-zero rescue is modelled too: when maxSurge and
    maxUnavailable both resolve to 0, Kubernetes forces maxUnavailable to 1
    "on the theory that surge might not work due to quota". Without that, a
    manifest with maxSurge 0 and maxUnavailable 25% is reported as an offender
    when Kubernetes would in fact roll it.
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


def _max_unavailable(path):
    """Resolved maxUnavailable of the LiteLLM Deployment in a plain YAML file."""
    doc = _litellm_deployment(path)
    if doc is None:
        raise AssertionError(f"no LiteLLM Deployment in {path}")
    return _resolve_max_unavailable(doc)


def _template_expression_for(template, field):
    """The Go-template expression a `<field>: {{ ... }}` line renders, or None.

    None means the field is absent or rendered as a literal; either way the
    caller's assertion is what should report it, not an exception here.
    """
    match = re.search(rf"{re.escape(field)}:\s*\{{\{{(.+?)\}}\}}", template)
    return match.group(1).strip() if match else None


def _resolve_template_variable(template, expression, hops=4):
    """Expand `$var` in `expression` through its assignments in `template`.

    One-hop indirection is what the template actually uses ($unavail from $ru);
    the hop limit is only there so a self-referential assignment cannot spin.
    Returns the expanded text, which the caller greps for a values path.
    """
    if expression is None:
        return ""
    seen = expression
    for _ in range(hops):
        expanded = seen
        for var in set(re.findall(r"\$[A-Za-z_][A-Za-z0-9_]*", seen)):
            # `:=` declares, `=` reassigns; both decide what the variable holds.
            for rhs in re.findall(
                rf"{re.escape(var)}\s*:?=\s*(.+?)\s*\}}\}}", template
            ):
                expanded += " " + rhs
        if expanded == seen:
            break
        seen = expanded
    return seen


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
        #
        # The strategy block renders through a variable rather than naming the
        # values path inline, because an unset fencepost has to pick up the
        # chart default before the both-zero guard reads it. So follow one hop
        # of assignment: matching only the inline form would fail on a template
        # that is correct, and matching any `{{ ... }}` at all would pass one
        # that renders a hard-coded variable.
        template = _CHART_TEMPLATE.read_text()
        source = _template_expression_for(template, "maxUnavailable")
        self.assertIsNotNone(
            source,
            "charts/kube-agents/templates/litellm.yaml must render maxUnavailable from a "
            "template expression, not a literal",
        )
        self.assertIn(
            ".Values.litellm.rollingUpdate",
            _resolve_template_variable(template, source),
            "charts/kube-agents/templates/litellm.yaml must render maxUnavailable from "
            "litellm.rollingUpdate, so an install can choose its own value; it renders "
            f"{source!r}, which does not trace back to that value",
        )
        self.assertNotRegex(
            template,
            r"maxUnavailable:\s*\d",
            "charts/kube-agents/templates/litellm.yaml must not hardcode maxUnavailable; "
            "a literal would pin every install regardless of its quota",
        )

    # The next two overlap the sweep below, which visits both files. They are
    # kept because a failure that names the file and why it matters reads
    # better than one entry in a list, and because the sweep's roots could be
    # narrowed later without anyone noticing these two stopped being covered.

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
        # Another copy added later would not be caught by the cases above.
        #
        # Omitting `strategy` is a way to reintroduce this, not a way to avoid
        # it: Kubernetes defaults to RollingUpdate at 25%/25% and rounds the
        # maxUnavailable percentage down, so an absent block resolves to 0 at
        # 1 to 3 replicas. _resolve_max_unavailable models that, which is why
        # this sweeps resolved values instead of grepping for a literal.
        #
        # Two roots rather than the whole tree: these are where every plain
        # LiteLLM manifest lives today, and charts/**/templates holds Go
        # templates that are not parseable as YAML. A copy added under deploy/
        # or terraform/ would be missed.
        offenders = []
        roots = [
            _ROOT / "k8s-operator" / "config" / "integrations" / "litellm",
            _ROOT / "examples",
        ]
        for root in roots:
            for path in sorted(root.rglob("*.yaml")):
                doc = _litellm_deployment(path)
                if doc is None:
                    continue
                resolved = _resolve_max_unavailable(doc)
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
