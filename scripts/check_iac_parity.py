#!/usr/bin/env python3
"""Verify the three install surfaces still agree on the values they share.

kube-agents can be installed three ways, and each spells the same install out
in its own language:

* the **provisioning scripts** (``k8s-operator/scripts/``) plus the kustomize
  manifests they apply (``k8s-operator/config/``) — the source of truth;
* the **Terraform** modules and the ``full-install`` composition
  (``terraform/``);
* the **Helm chart** (``charts/kube-agents/``).

Nothing forces them to move together, and they have not: #542 bumped the
LiteLLM image everywhere except the chart, and #519 added model aliases the
chart never grew. Both survived review because reviewing a Terraform diff does
not put a chart default in front of you.

This script checks the mechanical subset — the scalar values two surfaces must
literally agree on. It cannot check intent; that is the ``review-iac-parity``
skill's job (``.agents/skills/review-iac-parity/SKILL.md``), and the two share
the divergence list below.

**Deliberate divergences, not checked here** (each documented where it lives):

* The chart runs the operator with ``ENABLE_WEBHOOKS=false`` — chart installs
  ship no cert-manager wiring. See the chart README's Notes.
* The chart rejects ``modelProvider: chatgpt``; that provider needs the
  kustomize overlay's OAuth-token PVC.
* The ``gke-cluster`` module builds an **Autopilot** cluster where
  ``provision_01_gcp_cluster.sh`` builds a Standard one, so node-level settings
  (machine type, gVisor node pool, managed-OTel scope) have no Terraform
  counterpart.
* LiteLLM's OTel callback is unconditional in the kustomize base and gated on
  ``litellm.otel`` in the chart, because a chart install may target a cluster
  with no managed collector.
* ``harness.hermes.dashboardEnabled`` defaults to ``true`` in the CRD and
  ``false`` on the script path. A real inconsistency, tracked in the chart
  README rather than papered over here.
* The ``github-minter`` module creates IAM and KMS only; importing the GitHub
  App PEM stays with ``provision_10_deploy_github_minter.sh``.

Standard library only, so it runs in CI and in a bare clone.

Usage::

    python3 scripts/check_iac_parity.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

COMMON_SH = REPO / "k8s-operator/scripts/common.sh"
PROVISION_01 = REPO / "k8s-operator/scripts/provision_01_gcp_cluster.sh"
PROVISION_04 = REPO / "k8s-operator/scripts/provision_04_gcp_iam.sh"
PROVISION_05 = REPO / "k8s-operator/scripts/provision_05_gcp_gchat.sh"
PROVISION_12 = REPO / "k8s-operator/scripts/provision_12_gke_backup_plan.sh"
LITELLM_DEPLOYMENT = REPO / "k8s-operator/config/integrations/litellm/base/deployment.yaml"
LITELLM_CONFIG = REPO / "k8s-operator/config/integrations/litellm/base/config.yaml"

CHART_VALUES = REPO / "charts/kube-agents/values.yaml"
CHART_LITELLM = REPO / "charts/kube-agents/templates/litellm.yaml"

TF_FULL_INSTALL = REPO / "terraform/examples/full-install/main.tf"
TF_IAM_VARS = REPO / "terraform/modules/kube-agents-iam/variables.tf"
TF_CLUSTER_VARS = REPO / "terraform/modules/gke-cluster/variables.tf"
TF_MINTER_VARS = REPO / "terraform/modules/github-minter/variables.tf"
TF_CHAT_VARS = REPO / "terraform/modules/chat-pubsub/variables.tf"
TF_BACKUP_VARS = REPO / "terraform/modules/gke-backup-plan/variables.tf"

# Stand-in for "whatever MODEL_DEFAULT_NAME resolves to", so the kustomize
# ${MODEL_DEFAULT_NAME} alias and the chart's {{ $model }} alias compare equal.
MODEL_PLACEHOLDER = "<default-model>"


class Failures(list):
    def add(self, check: str, detail: str) -> None:
        self.append((check, detail))


# ─── extraction helpers ───────────────────────────────────────────────────────


def read(path: Path) -> str:
    if not path.is_file():
        sys.exit(f"ERROR: expected file is missing: {path.relative_to(REPO)}")
    return path.read_text(encoding="utf-8")


def shell_assignment(text: str, name: str, path: Path) -> str:
    """Value of NAME="value" in a shell script.

    common.sh repeats its identifier exports in several branches; every
    occurrence has to agree, or the value depends on which branch ran and
    there is nothing single for the other surfaces to mirror.
    """
    values = re.findall(rf'^\s*(?:export\s+)?{re.escape(name)}="([^"]*)"', text, re.M)
    if not values:
        sys.exit(f"ERROR: no {name}= assignment in {path.relative_to(REPO)}")
    if len(set(values)) > 1:
        sys.exit(
            f"ERROR: {name} is assigned {sorted(set(values))} in "
            f"{path.relative_to(REPO)}; the parity check needs one value"
        )
    return values[0]


def init_var_default(text: str, name: str, path: Path) -> str:
    """Default of `init_var "NAME" "default" "prompt"` in a provisioning step."""
    match = re.search(rf'init_var\s+"{re.escape(name)}"\s+"([^"]*)"', text)
    if not match:
        sys.exit(f"ERROR: no init_var for {name} in {path.relative_to(REPO)}")
    return match.group(1)


def bash_array(text: str, name: str, path: Path) -> list[str]:
    """Elements of `local name=( "a" "b" )`, comments stripped."""
    match = re.search(rf"{re.escape(name)}=\(\s*(.*?)\)", text, re.S)
    if not match:
        sys.exit(f"ERROR: no {name}=( ... ) array in {path.relative_to(REPO)}")
    return re.findall(r'"([^"]+)"', match.group(1))


def tf_list(text: str, assignment: str, path: Path) -> list[str]:
    """Elements of a Terraform `name = [ "a", "b" ]` list, comments stripped."""
    match = re.search(rf"{re.escape(assignment)}\s*=\s*\[(.*?)\]", text, re.S)
    if not match:
        sys.exit(f"ERROR: no {assignment} = [ ... ] list in {path.relative_to(REPO)}")
    body = re.sub(r"#.*", "", match.group(1))
    return re.findall(r'"([^"]+)"', body)


def tf_variable_default(text: str, name: str, path: Path) -> str | list[str]:
    """Default of a Terraform `variable "name" { ... default = ... }` block."""
    block = re.search(
        rf'variable\s+"{re.escape(name)}"\s*\{{(.*?)\n\}}', text, re.S
    )
    if not block:
        sys.exit(f"ERROR: no variable {name!r} in {path.relative_to(REPO)}")
    body = block.group(1)
    listed = re.search(r"default\s*=\s*\[(.*?)\]", body, re.S)
    if listed:
        return re.findall(r'"([^"]+)"', re.sub(r"#.*", "", listed.group(1)))
    scalar = re.search(r"default\s*=\s*(.+)", body)
    if not scalar:
        sys.exit(f"ERROR: variable {name!r} has no default in {path.relative_to(REPO)}")
    return scalar.group(1).strip().strip('"')


def simple_yaml(text: str) -> dict:
    """Parse the `key: value` subset values.yaml is written in.

    Nested maps by indentation, scalars as strings, everything else (list
    items, block scalars) skipped — values.yaml uses none of it, and a parser
    that quietly mangled them would be worse than one that ignores them.
    """
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or raw.lstrip().startswith("-"):
            continue
        indent = len(raw) - len(raw.lstrip())
        match = re.match(r"([A-Za-z_][\w.-]*):\s*(.*)$", raw.strip())
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        value = re.sub(r"\s+#.*$", "", value).strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if value == "":
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = value.strip('"').strip("'")
    return root


def dig(tree: dict, path: str):
    node = tree
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            sys.exit(f"ERROR: {CHART_VALUES.relative_to(REPO)} has no key {path}")
        node = node[part]
    return node


def model_names(text: str) -> list[str]:
    """`model_name:` aliases in a LiteLLM config, placeholders normalised."""
    names = []
    for name in re.findall(r"model_name:\s*(\S.*?)\s*$", text, re.M):
        if name in ("${MODEL_DEFAULT_NAME}", "{{ $model }}"):
            name = MODEL_PLACEHOLDER
        names.append(name)
    return names


# ─── checks ───────────────────────────────────────────────────────────────────


def check_litellm_image(f: Failures) -> None:
    kustomize = read(LITELLM_DEPLOYMENT)
    match = re.search(r"image:\s*(\S+/litellm):(\S+)", kustomize)
    if not match:
        sys.exit(f"ERROR: no litellm image in {LITELLM_DEPLOYMENT.relative_to(REPO)}")
    repo, tag = match.group(1), match.group(2)

    values = simple_yaml(read(CHART_VALUES))
    chart_repo = dig(values, "litellm.image.repository")
    chart_tag = dig(values, "litellm.image.tag")
    if (chart_repo, chart_tag) != (repo, tag):
        f.add(
            "litellm-image",
            f"chart pins {chart_repo}:{chart_tag}, kustomize base pins {repo}:{tag} "
            f"({CHART_VALUES.relative_to(REPO)} vs {LITELLM_DEPLOYMENT.relative_to(REPO)})",
        )

    # The example manifests are copies of the same gateway; a version bump that
    # skips them leaves users pasting an old image.
    for example in sorted(REPO.glob("examples/litellm-*/deployment.yaml")):
        found = re.search(r"image:\s*\S+/litellm:(\S+)", example.read_text(encoding="utf-8"))
        if found and found.group(1) != tag:
            f.add(
                "litellm-image",
                f"{example.relative_to(REPO)} pins {found.group(1)}, kustomize base pins {tag}",
            )


def check_litellm_aliases(f: Failures) -> None:
    kustomize = model_names(read(LITELLM_CONFIG))
    chart = model_names(read(CHART_LITELLM))
    if sorted(kustomize) != sorted(chart):
        f.add(
            "litellm-model-aliases",
            f"chart serves {chart}, kustomize base serves {kustomize} "
            f"({CHART_LITELLM.relative_to(REPO)} vs {LITELLM_CONFIG.relative_to(REPO)})",
        )


def check_model_defaults(f: Failures) -> None:
    """common.sh's per-provider default model vs the chart's dict."""
    text = read(COMMON_SH)
    body = re.search(r"default_model_for_provider\(\)\s*\{(.*?)\n\}", text, re.S)
    if not body:
        sys.exit(f"ERROR: no default_model_for_provider in {COMMON_SH.relative_to(REPO)}")
    script: dict[str, str] = {}
    for patterns, model in re.findall(r"^\s*([^\s)]+(?:\s*\|\s*[^\s)]+)*)\)\s*echo\s+\"([^\"]+)\"", body.group(1), re.M):
        for provider in (p.strip() for p in patterns.split("|")):
            # `*)` is the case default, which common.sh uses for gemini.
            script["gemini" if provider == "*" else provider] = model

    chart_line = re.search(r"\$defaultModels\s*:=\s*dict\s+(.+?)\}\}", read(CHART_LITELLM))
    if not chart_line:
        sys.exit(f"ERROR: no $defaultModels dict in {CHART_LITELLM.relative_to(REPO)}")
    pairs = re.findall(r'"([^"]+)"', chart_line.group(1))
    chart = dict(zip(pairs[::2], pairs[1::2]))

    # chatgpt is chart-rejected by design, so compare only shared providers.
    for provider, model in sorted(chart.items()):
        if provider not in script:
            f.add("model-defaults", f"chart knows provider {provider!r}, common.sh does not")
        elif script[provider] != model:
            f.add(
                "model-defaults",
                f"{provider}: chart defaults to {model}, common.sh to {script[provider]}",
            )


def check_registry_prefix(f: Failures) -> None:
    prefix = shell_assignment(read(COMMON_SH), "DEFAULT_REGISTRY_PREFIX", COMMON_SH)
    values = simple_yaml(read(CHART_VALUES))
    for key, image in (
        ("operator.image.repository", "k8s-operator"),
        ("platformAgent.deployment.image.repository", "platform-agent"),
    ):
        actual = dig(values, key)
        if actual != f"{prefix}/{image}":
            f.add(
                "registry-prefix",
                f"chart {key} is {actual}, common.sh's DEFAULT_REGISTRY_PREFIX implies "
                f"{prefix}/{image}",
            )


def check_iam_roles(f: Failures) -> None:
    script = read(PROVISION_04)
    read_only = bash_array(script, "local read_only_roles", PROVISION_04)
    gke_admin = bash_array(script, "local gke_admin_roles", PROVISION_04)

    module_default = tf_variable_default(read(TF_IAM_VARS), "project_roles", TF_IAM_VARS)
    if list(module_default) != read_only:
        f.add(
            "iam-roles",
            f"kube-agents-iam project_roles default {sorted(set(module_default) ^ set(read_only))} "
            f"differs from provision_04's read_only_roles",
        )

    composition = read(TF_FULL_INSTALL)
    for name, expected in (("read_only_roles", read_only), ("gke_admin_roles", gke_admin)):
        actual = tf_list(composition, name, TF_FULL_INSTALL)
        if actual != expected:
            f.add(
                "iam-roles",
                f"full-install local.{name} differs from provision_04's {name}: "
                f"{sorted(set(actual) ^ set(expected))}",
            )


def check_identifiers(f: Failures) -> None:
    """GSA/KSA/namespace/topic names both paths have to pick identically."""
    common = read(COMMON_SH)
    namespace = shell_assignment(common, "NAMESPACE", COMMON_SH)
    agent_ksa = shell_assignment(common, "PLATFORM_AGENT_KSA_NAME", COMMON_SH)
    agent_gsa = shell_assignment(common, "PLATFORM_AGENT_GSA_NAME", COMMON_SH)
    minter_ksa = shell_assignment(common, "GITHUB_MINTER_KSA_NAME", COMMON_SH)
    minter_gsa = shell_assignment(common, "GITHUB_MINTER_GSA_NAME", COMMON_SH)

    iam_vars = read(TF_IAM_VARS)
    minter_vars = read(TF_MINTER_VARS)
    expectations = [
        ("kube-agents-iam namespace", tf_variable_default(iam_vars, "namespace", TF_IAM_VARS), namespace),
        ("kube-agents-iam ksa_name", tf_variable_default(iam_vars, "ksa_name", TF_IAM_VARS), agent_ksa),
        ("kube-agents-iam service_account_id", tf_variable_default(iam_vars, "service_account_id", TF_IAM_VARS), agent_gsa),
        ("github-minter namespace", tf_variable_default(minter_vars, "namespace", TF_MINTER_VARS), namespace),
        ("github-minter ksa_name", tf_variable_default(minter_vars, "ksa_name", TF_MINTER_VARS), minter_ksa),
        ("github-minter service_account_id", tf_variable_default(minter_vars, "service_account_id", TF_MINTER_VARS), minter_gsa),
    ]

    values = simple_yaml(read(CHART_VALUES))
    expectations.append(
        ("chart platformAgent.security.serviceAccountName", dig(values, "platformAgent.security.serviceAccountName"), agent_ksa)
    )

    chat = read(PROVISION_05)
    chat_vars = read(TF_CHAT_VARS)
    topic = init_var_default(chat, "CHAT_TOPIC_NAME", PROVISION_05)
    subscription = init_var_default(chat, "CHAT_SUB_NAME", PROVISION_05)
    expectations += [
        ("chat-pubsub topic_name", tf_variable_default(chat_vars, "topic_name", TF_CHAT_VARS), topic),
        ("chat-pubsub subscription_name", tf_variable_default(chat_vars, "subscription_name", TF_CHAT_VARS), subscription),
        ("chart googleChat.topicName", dig(values, "platformAgent.integration.googleChat.topicName"), topic),
        ("chart googleChat.subscriptionName", dig(values, "platformAgent.integration.googleChat.subscriptionName"), subscription),
    ]

    for label, actual, expected in expectations:
        if actual != expected:
            f.add("identifiers", f"{label} is {actual!r}, the scripts use {expected!r}")


def check_kms_names(f: Failures) -> None:
    cluster_vars = read(TF_CLUSTER_VARS)
    minter_vars = read(TF_MINTER_VARS)
    pairs = [
        (
            "gke-cluster kms_keyring_name",
            tf_variable_default(cluster_vars, "kms_keyring_name", TF_CLUSTER_VARS),
            init_var_default(read(PROVISION_01), "GKE_DB_KMS_KEYRING", PROVISION_01),
        ),
        (
            "gke-cluster kms_key_name",
            tf_variable_default(cluster_vars, "kms_key_name", TF_CLUSTER_VARS),
            init_var_default(read(PROVISION_01), "GKE_DB_KMS_KEY", PROVISION_01),
        ),
        (
            "github-minter kms_keyring_name",
            tf_variable_default(minter_vars, "kms_keyring_name", TF_MINTER_VARS),
            init_var_default(read(PROVISION_04), "KMS_KEYRING", PROVISION_04),
        ),
        (
            "github-minter kms_key_name",
            tf_variable_default(minter_vars, "kms_key_name", TF_MINTER_VARS),
            init_var_default(read(PROVISION_04), "KMS_KEY", PROVISION_04),
        ),
    ]
    for label, actual, expected in pairs:
        if actual != expected:
            f.add("kms-names", f"{label} is {actual!r}, the scripts use {expected!r}")


def check_backup_plan(f: Failures) -> None:
    script = read(PROVISION_12)
    module = read(TF_BACKUP_VARS)
    pairs = [
        (
            "gke-backup-plan cron_schedule",
            tf_variable_default(module, "cron_schedule", TF_BACKUP_VARS),
            init_var_default(script, "BACKUP_CRON_SCHEDULE", PROVISION_12),
        ),
        (
            "gke-backup-plan backup_retain_days",
            str(tf_variable_default(module, "backup_retain_days", TF_BACKUP_VARS)),
            init_var_default(script, "BACKUP_RETAIN_DAYS", PROVISION_12),
        ),
        (
            "gke-backup-plan selected_namespaces",
            tf_variable_default(module, "selected_namespaces", TF_BACKUP_VARS),
            [shell_assignment(read(COMMON_SH), "NAMESPACE", COMMON_SH)],
        ),
    ]
    for label, actual, expected in pairs:
        if actual != expected:
            f.add("backup-plan", f"{label} is {actual!r}, the scripts use {expected!r}")

    # The script derives the plan name; the module must derive the same one.
    script_name = re.search(r'BACKUP_PLAN_NAME="\$\{CLUSTER_NAME\}([^"]*)"', script)
    module_name = re.search(r'"\$\{var\.cluster_name\}([^"]*)"', read(REPO / "terraform/modules/gke-backup-plan/main.tf"))
    if not script_name or not module_name:
        f.add("backup-plan", "could not read the derived BackupPlan name from both sides")
    elif script_name.group(1) != module_name.group(1):
        f.add(
            "backup-plan",
            f"module derives <cluster>{module_name.group(1)!r}, the script derives "
            f"<cluster>{script_name.group(1)!r}",
        )


def check_litellm_replicas(f: Failures) -> None:
    kustomize = read(LITELLM_DEPLOYMENT)
    match = re.search(r"^\s*replicas:\s*(\d+)", kustomize, re.M)
    if not match:
        sys.exit(f"ERROR: no replicas in {LITELLM_DEPLOYMENT.relative_to(REPO)}")
    chart = dig(simple_yaml(read(CHART_VALUES)), "litellm.replicaCount")
    if chart != match.group(1):
        f.add(
            "litellm-replicas",
            f"chart litellm.replicaCount is {chart}, kustomize base runs {match.group(1)}",
        )


CHECKS = (
    check_litellm_image,
    check_litellm_aliases,
    check_model_defaults,
    check_registry_prefix,
    check_iam_roles,
    check_identifiers,
    check_kms_names,
    check_backup_plan,
    check_litellm_replicas,
)


def main() -> int:
    failures = Failures()
    for check in CHECKS:
        check(failures)

    if failures:
        print(f"{len(failures)} IaC parity problem(s) between the install surfaces:")
        for name, detail in failures:
            print(f"  {name}: {detail}")
        print(
            "\nThe provisioning scripts and k8s-operator/config are the source of truth. "
            "Update the chart and Terraform to match, or — if the divergence is "
            "deliberate — document it and add it to the exemption list in this "
            "script's docstring and in .agents/skills/review-iac-parity/SKILL.md."
        )
        return 1

    print(f"IaC parity: {len(CHECKS)} checks passed across scripts, Terraform, and the Helm chart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
