#!/usr/bin/env python3
"""Unit tests for the extractors in ``check_iac_parity.py``.

The checks themselves are self-verifying: they compare two real files, so a
broken comparison shows up as a CI failure on a repository that is actually in
sync. The extractors are not. Roughly two thirds of that script is bespoke
parsing — a YAML subset, Terraform variable blocks and lists, bash arrays,
``init_var`` defaults, LiteLLM aliases — and a parser that stops matching fails
loudly (``sys.exit``) while a parser that matches the *wrong text* does not: it
hands the comparison a plausible value, both sides "agree", and CI reports
parity across surfaces that have drifted. That is the failure these tests
exist to catch, and every case below is written against it.

Run with ``make test-python`` (the Makefile discovers ``scripts/test_*.py``) or
directly::

    python3 -m unittest scripts.test_check_iac_parity -v
"""

from __future__ import annotations

import importlib.util
import textwrap
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "check_iac_parity", Path(__file__).with_name("check_iac_parity.py")
)
parity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(parity)

# Inside the repo root: the extractors' error messages call
# path.relative_to(REPO), which raises on a path from anywhere else.
FAKE = parity.REPO / "fake" / "source.tf"


class SimpleYamlTest(unittest.TestCase):
    def test_nests_by_indentation_and_strips_comments(self):
        tree = parity.simple_yaml(
            textwrap.dedent(
                """\
                # leading comment
                litellm:
                  image:
                    repository: ghcr.io/berriai/litellm
                    tag: v1.95.0 # trailing comment
                  replicaCount: 2
                platformAgent:
                  name: platform-agent
                """
            )
        )
        self.assertEqual(tree["litellm"]["image"]["tag"], "v1.95.0")
        self.assertEqual(tree["litellm"]["image"]["repository"], "ghcr.io/berriai/litellm")
        self.assertEqual(tree["litellm"]["replicaCount"], "2")
        self.assertEqual(tree["platformAgent"]["name"], "platform-agent")

    def test_dedent_pops_back_to_the_right_parent(self):
        """A sibling after a nested block must not be filed under the nephew."""
        tree = parity.simple_yaml(
            textwrap.dedent(
                """\
                operator:
                  image:
                    tag: a
                  replicaCount: 1
                litellm:
                  enabled: true
                """
            )
        )
        self.assertEqual(tree["operator"]["replicaCount"], "1")
        self.assertNotIn("replicaCount", tree["operator"]["image"])
        self.assertEqual(tree["litellm"]["enabled"], "true")

    def test_list_items_are_skipped_not_misfiled(self):
        tree = parity.simple_yaml(
            textwrap.dedent(
                """\
                operator:
                  extraEnv:
                    - name: FLUENT_BIT_IMAGE
                      value: registry/mirror
                  replicaCount: 1
                """
            )
        )
        self.assertEqual(tree["operator"]["replicaCount"], "1")
        self.assertNotIn("name", tree["operator"])

    def test_quotes_are_stripped(self):
        tree = parity.simple_yaml('a:\n  b: "quoted"\n  c: \'single\'\n')
        self.assertEqual(tree["a"]["b"], "quoted")
        self.assertEqual(tree["a"]["c"], "single")


class TerraformVariableTest(unittest.TestCase):
    def test_scalar_default(self):
        text = textwrap.dedent(
            """\
            variable "topic_name" {
              description = "Pub/Sub topic"
              type        = string
              default     = "platform-agent-chat-events"
            }
            """
        )
        self.assertEqual(
            parity.tf_variable_default(text, "topic_name", FAKE),
            "platform-agent-chat-events",
        )

    def test_list_default_drops_comments(self):
        text = textwrap.dedent(
            """\
            variable "project_roles" {
              type = list(string)
              default = [
                "roles/container.viewer",
                # a commented-out role must not be extracted
                # "roles/owner",
                "roles/logging.viewer",
              ]
            }
            """
        )
        self.assertEqual(
            parity.tf_variable_default(text, "project_roles", FAKE),
            ["roles/container.viewer", "roles/logging.viewer"],
        )

    def test_validation_mentioning_default_does_not_win(self):
        """The regression the anchoring exists for.

        An unanchored `default\\s*=` also matches inside a validation block. The
        extractor would then return "must not be default = unset", the
        comparison against the scripts would fail on a repository that is
        perfectly in sync, and the fix would look like a source-of-truth change.
        """
        text = textwrap.dedent(
            """\
            variable "mode" {
              type    = string
              default = "quiet"

              validation {
                condition     = var.mode != ""
                error_message = "mode must not be default = unset."
              }
            }
            """
        )
        self.assertEqual(parity.tf_variable_default(text, "mode", FAKE), "quiet")

    def test_block_scan_stops_at_its_own_closing_brace(self):
        """A variable with no default must not borrow the next variable's."""
        text = textwrap.dedent(
            """\
            variable "first" {
              type = string
            }

            variable "second" {
              type    = string
              default = "second-value"
            }
            """
        )
        with self.assertRaises(SystemExit):
            parity.tf_variable_default(text, "first", FAKE)

    def test_missing_variable_exits(self):
        with self.assertRaises(SystemExit):
            parity.tf_variable_default('variable "other" {\n  type = string\n}\n', "absent", FAKE)


class TerraformListTest(unittest.TestCase):
    def test_preserves_order_and_ignores_commented_entries(self):
        text = textwrap.dedent(
            """\
            locals {
              gke_admin_roles = [
                "roles/container.clusterAdmin",
                # The agent must not administer the audit-log sink.
                # "roles/logging.admin",
                "roles/logging.viewer",
              ]
            }
            """
        )
        self.assertEqual(
            parity.tf_list(text, "gke_admin_roles", FAKE),
            ["roles/container.clusterAdmin", "roles/logging.viewer"],
        )

    def test_missing_list_exits(self):
        with self.assertRaises(SystemExit):
            parity.tf_list("locals {\n}\n", "absent_roles", FAKE)


class ShellExtractorTest(unittest.TestCase):
    def test_bash_array_ignores_comments(self):
        text = textwrap.dedent(
            """\
            get_roles() {
              local read_only_roles=(
                "roles/container.viewer"
                # "roles/owner"
                "roles/logging.viewer"
              )
            }
            """
        )
        self.assertEqual(
            parity.bash_array(text, "local read_only_roles", FAKE),
            ["roles/container.viewer", "roles/logging.viewer"],
        )

    def test_init_var_default(self):
        text = 'init_var "BACKUP_CRON_SCHEDULE" "0 2 * * *" "Enter cron"\n'
        self.assertEqual(
            parity.init_var_default(text, "BACKUP_CRON_SCHEDULE", FAKE), "0 2 * * *"
        )

    def test_init_var_empty_default_is_a_value_not_a_miss(self):
        text = 'init_var "GITHUB_ORG" "" "Enter GitHub Organization"\n'
        self.assertEqual(parity.init_var_default(text, "GITHUB_ORG", FAKE), "")

    def test_shell_assignment_accepts_repeated_agreeing_definitions(self):
        """common.sh exports its identifiers in several branches."""
        text = textwrap.dedent(
            """\
            if a; then
              export NAMESPACE="kubeagents-system"
            else
              export NAMESPACE="kubeagents-system"
            fi
            """
        )
        self.assertEqual(parity.shell_assignment(text, "NAMESPACE", FAKE), "kubeagents-system")

    def test_shell_assignment_rejects_disagreeing_definitions(self):
        """Two values means no single value for the other surfaces to mirror."""
        text = textwrap.dedent(
            """\
            export NAMESPACE="kubeagents-system"
            export NAMESPACE="something-else"
            """
        )
        with self.assertRaises(SystemExit):
            parity.shell_assignment(text, "NAMESPACE", FAKE)

    def test_shell_assignment_missing_exits(self):
        with self.assertRaises(SystemExit):
            parity.shell_assignment("export OTHER=1\n", "NAMESPACE", FAKE)


class ModelNamesTest(unittest.TestCase):
    def test_kustomize_placeholder_normalises(self):
        text = textwrap.dedent(
            """\
            model_list:
              - model_name: model-default
              - model_name: hermes-agent
              - model_name: ${MODEL_DEFAULT_NAME}
            """
        )
        self.assertEqual(
            parity.model_names(text, FAKE),
            ["model-default", "hermes-agent", parity.MODEL_PLACEHOLDER],
        )

    def test_chart_placeholder_normalises_to_the_same_token(self):
        """Both spellings the chart has used must compare equal to kustomize's."""
        for spelling in ("{{ .model }}", "{{ $model }}"):
            with self.subTest(spelling=spelling):
                self.assertEqual(
                    parity.model_names(f"  - model_name: {spelling}\n", FAKE),
                    [parity.MODEL_PLACEHOLDER],
                )

    def test_no_aliases_exits_rather_than_reporting_none(self):
        """The config moving to another file must not read as 'serves nothing'."""
        with self.assertRaises(SystemExit):
            parity.model_names("apiVersion: v1\nkind: ConfigMap\n", FAKE)


class DigTest(unittest.TestCase):
    def test_missing_key_exits(self):
        with self.assertRaises(SystemExit):
            parity.dig({"litellm": {"image": {}}}, "litellm.image.tag")

    def test_walks_nested_path(self):
        self.assertEqual(parity.dig({"a": {"b": {"c": "v"}}}, "a.b.c"), "v")


class EndToEndTest(unittest.TestCase):
    def test_every_check_passes_against_the_real_tree(self):
        """The checks are only meaningful if the repository itself is in sync."""
        failures = parity.Failures()
        for check in parity.CHECKS:
            check(failures)
        self.assertEqual(list(failures), [], f"parity failures: {list(failures)}")


if __name__ == "__main__":
    unittest.main()
