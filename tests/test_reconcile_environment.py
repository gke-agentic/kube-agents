"""The reconcile and drift-detection path for the long-lived environments.

#1117: `autopush` and `staging` were updated by image tag only. Nothing ever ran
Terraform against them, so a month of infrastructure changes on `main` — IAM,
Pub/Sub, node pools, the chart values the composition renders — was invisible in
both while a green redeploy reported "main is deployed".

Every assertion here is a way that fix goes silently wrong: a plan that mutates,
a plan that leaks a credential into a public artifact, an apply that starts
while somebody is live-testing, a reconcile that races the redeploy workflows
for the same Helm release.
"""

import importlib.util
import pathlib
import re
import subprocess
import unittest

import yaml

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"
_RECONCILE_WF = _WORKFLOWS / "reconcile-environment.yml"
_DRIFT_WF = _WORKFLOWS / "drift-detect.yml"
_DEPLOY_WF = _WORKFLOWS / "deploy-environment.yml"
_LIFECYCLE = _REPO_ROOT / "terraform" / "examples" / "full-install" / "lifecycle.sh"
_TF_VARIABLES = _REPO_ROOT / "terraform" / "examples" / "full-install" / "variables.tf"
_RECONCILE_SH = _REPO_ROOT / "scripts" / "release" / "reconcile_environment.sh"
_UPGRADE_SH = _REPO_ROOT / "upgrade.sh"

_LONG_LIVED = ("autopush", "staging")


def _doc(path):
    return yaml.safe_load(path.read_text())


def _load_report_drift():
    spec = importlib.util.spec_from_file_location(
        "report_drift", _REPO_ROOT / "scripts" / "release" / "report_drift.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PubSubIamBindingTest(unittest.TestCase):
    """#1059. GCP purges a topic's IAM policy when the topic is recreated.

    A binding keyed on `.name` is known at plan time, so it is excluded from the
    plan that replaces its parent — the apply goes green and the live policy is
    empty. `.id` is computed, so a replacement renders it unknown and pulls the
    binding into the plan with it.
    """

    def test_no_pubsub_iam_member_binds_to_a_name(self):
        pattern = re.compile(
            r"^\s*(?:topic|subscription)\s*=\s*google_pubsub_\w+\.\w+\.name\s*$",
            re.MULTILINE)
        for path in sorted((_REPO_ROOT / "terraform").rglob("*.tf")):
            text = path.read_text()
            if "google_pubsub_topic_iam_member" not in text and \
               "google_pubsub_subscription_iam_member" not in text:
                continue
            with self.subTest(module=str(path.relative_to(_REPO_ROOT))):
                self.assertEqual(
                    pattern.findall(text), [],
                    "bind IAM members to the parent's .id, not .name — see #1059")


class LifecyclePlanTest(unittest.TestCase):
    def setUp(self):
        self.text = _LIFECYCLE.read_text()
        # Everything between `plan)` and the `apply)` case that follows it.
        match = re.search(r"^  plan\)$(.*?)^  apply\)$", self.text,
                          re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, "lifecycle.sh has no plan subcommand")
        # Comments stripped, because this branch's comments name every one of
        # the commands below in the course of explaining why it does not run
        # them.
        self.branch = "\n".join(
            line for line in match.group(1).splitlines()
            if not line.lstrip().startswith("#"))

    def test_the_plan_branch_runs_nothing_that_writes(self):
        """A drift report that changes the thing it reports on is worse than none."""
        for forbidden in ("terraform apply", "terraform import", "terraform destroy",
                          "adopt_kms", "adopt_pubsub", "buckets create"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.branch)

    def test_the_plan_takes_no_state_lock(self):
        """So a plan can neither block nor be blocked by the apply it reports on."""
        self.assertIn("-lock=false", self.branch)

    def test_the_plan_does_not_create_the_state_bucket(self):
        """An empty bucket plans the whole composition as new, which reads as total drift."""
        self.assertIn("ensure_init readonly", self.branch)
        self.assertIn('if [[ "$mode" == "readonly" ]]; then', self.text)

    def test_detailed_exitcode_is_not_forced_on(self):
        """It changes what a non-zero exit means; only a caller expecting that may ask."""
        self.assertNotIn("-detailed-exitcode", self.branch)

    def test_apply_adopts_pre_existing_pubsub_resources(self):
        """#1061: creating a topic that exists is a 409, not a no-op."""
        apply_branch = re.search(r"^  apply\)$(.*?)^  destroy\)$", self.text,
                                 re.MULTILINE | re.DOTALL).group(1)
        self.assertIn("adopt_pubsub", apply_branch)

    def test_the_usage_range_still_covers_the_whole_header(self):
        """The fallback branch prints a fixed line range, so a longer header truncates it."""
        printed = re.search(r"sed -n '2,(\d+)p'", self.text)
        self.assertIsNotNone(printed)
        last = int(printed.group(1))
        lines = self.text.splitlines()
        # The header ends at the last comment line before `set -euo pipefail`.
        end = next(i for i, line in enumerate(lines, start=1)
                   if line.startswith("set -euo pipefail")) - 1
        self.assertEqual(last, end,
                         "update the sed range when the header comment changes length")


class PlanArtifactSafetyTest(unittest.TestCase):
    """The plan text is published as an artifact and quoted into an issue."""

    def test_every_credential_variable_is_marked_sensitive(self):
        """Terraform prints an unmarked variable's value in the plan, in full."""
        text = _TF_VARIABLES.read_text()
        blocks = re.findall(r'^variable "(\w+)" \{(.*?)^\}', text,
                            re.MULTILINE | re.DOTALL)
        secretish = re.compile(r"(_key|_token|_secret|_password|api_key|salt)$")
        # A KMS key is named by its resource path, not by its material: the path
        # is not a credential and is worth seeing in a plan. Anything else
        # ending in _key is.
        identifier = re.compile(r"kms|encryption")
        for name, body in blocks:
            if not secretish.search(name) or identifier.search(name):
                continue
            with self.subTest(variable=name):
                self.assertIn("sensitive   = true", body,
                              f"{name} would be printed in full in a published plan")


class ReconcileWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.doc = _doc(_RECONCILE_WF)
        self.job = self.doc["jobs"]["reconcile"]

    def test_it_is_guarded_against_forks(self):
        """A fork inherits workflow_dispatch and none of the credentials."""
        self.assertIn("github.repository == 'gke-labs/kube-agents'", self.job["if"])

    def test_it_shares_the_environment_lock_with_the_teardown_workflow(self):
        """A reconcile and a rebuild of the same cluster must never overlap."""
        self.assertEqual(
            self.job["concurrency"]["group"],
            _doc(_DEPLOY_WF)["jobs"]["deploy-environment"]["concurrency"]["group"])

    def test_the_terraform_wrapper_is_off(self):
        """The wrapper does not preserve `plan -detailed-exitcode`, which is the verdict."""
        setup = next(step for step in self.job["steps"]
                     if str(step.get("uses", "")).startswith("hashicorp/setup-terraform@"))
        self.assertIs(setup["with"]["terraform_wrapper"], False)

    def test_every_third_party_action_is_pinned_to_a_sha(self):
        for step in self.job["steps"]:
            uses = str(step.get("uses", ""))
            if not uses or uses.startswith("./"):
                continue
            with self.subTest(uses=uses):
                self.assertRegex(uses.split("@")[1].split()[0], r"^[0-9a-f]{40}$")

    def test_it_can_read_the_actions_api(self):
        """The in-flight redeploy check is a `gh run list`."""
        self.assertEqual(self.job["permissions"].get("actions"), "read")


class DriftWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.doc = _doc(_DRIFT_WF)

    def test_it_only_ever_plans(self):
        """Read-only is what lets it run on a schedule against a shared install."""
        self.assertEqual(self.doc["jobs"]["plan"]["with"]["mode"], "plan")

    def test_it_covers_both_long_lived_environments(self):
        """§9: fixing autopush alone leaves staging exactly as stale."""
        matrix = self.doc["jobs"]["plan"]["strategy"]["matrix"]["environment"]
        self.assertEqual(sorted(matrix), sorted(_LONG_LIVED))

    def test_a_failing_environment_does_not_cancel_the_other(self):
        self.assertIs(self.doc["jobs"]["plan"]["strategy"]["fail-fast"], False)

    def test_it_pins_no_image_tag(self):
        """Otherwise the report conflates image lag with infrastructure drift."""
        self.assertEqual(self.doc["jobs"]["plan"]["with"]["image_tag"], "")

    def test_it_is_guarded_against_forks(self):
        """A fork inherits `schedule` and mails its owner on every failure."""
        for name, job in self.doc["jobs"].items():
            with self.subTest(job=name):
                self.assertIn("github.repository == 'gke-labs/kube-agents'", job["if"])

    def test_it_runs_on_a_schedule(self):
        """The whole point: nothing said autopush was a month behind."""
        self.assertIn("schedule", self.doc[True])


class DeployEnvironmentGuardTest(unittest.TestCase):
    """Option B: the same workflow now also destroys a long-lived environment."""

    def setUp(self):
        self.doc = _doc(_DEPLOY_WF)
        self.steps = self.doc["jobs"]["deploy-environment"]["steps"]

    def test_the_long_lived_environments_are_dispatchable(self):
        options = self.doc[True]["workflow_dispatch"]["inputs"]["github_environment"]["options"]
        for env in _LONG_LIVED:
            with self.subTest(environment=env):
                self.assertIn(env, options)

    def test_a_long_lived_teardown_needs_typed_confirmation(self):
        step = next(s for s in self.steps
                    if s["name"] == "Confirm a long-lived environment teardown")
        for env in _LONG_LIVED:
            self.assertIn(env, step["if"])
        self.assertIn('"${CONFIRM}" != "${TARGET}"', step["run"])

    def test_the_confirmation_runs_before_anything_costly(self):
        """It is a typo check; everything after the checkout costs minutes."""
        names = [s.get("name", "") for s in self.steps]
        self.assertEqual(names[0], "Confirm a long-lived environment teardown")

    def test_it_refuses_unless_the_live_test_lease_is_positively_free(self):
        """The lease ConfigMap lives in the cluster this is about to destroy.

        Matching "free" rather than negating "held" is what keeps an
        unreachable cluster — a transient API-server error, an expired auth
        plugin — from reading as an idle one. `status --json` reports that as
        its own state precisely so a caller can tell the two apart.
        """
        step = next(s for s in self.steps
                    if s["name"] == "Refuse while somebody is live-testing")
        self.assertIn("live_test_lease.py status --json", step["run"])
        self.assertIn('grep -q \'"state": "free"\'', step["run"])
        self.assertNotIn('grep -q \'"state": "held"\'', step["run"])


class ReconcileScriptTest(unittest.TestCase):
    def setUp(self):
        self.text = _RECONCILE_SH.read_text()

    def test_it_is_valid_bash(self):
        proc = subprocess.run(["bash", "-n", str(_RECONCILE_SH)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_it_renders_the_configuration_strictly(self):
        """A long-lived environment missing a setting must fail, not default."""
        self.assertIn("render_install_env.sh", self.text)
        self.assertIn("--strict", self.text)

    def test_only_an_apply_takes_the_lease(self):
        """A plan changes nothing, so holding the lease would only block people."""
        self.assertIn('if [ "$MODE" = "apply" ] && [ "$LEASE_POLICY" != "ignore" ]', self.text)

    def test_the_lease_is_claimed_rather_than_polled(self):
        """A separate "is it free?" read leaves a window before the apply."""
        self.assertRegex(self.text, r'live_test_lease\.py"?\s+acquire')
        self.assertIn("trap release_lease EXIT", self.text)

    def test_an_apply_waits_out_an_in_flight_redeploy(self):
        """Both drive `helm upgrade` on the release the composition owns."""
        self.assertIn("await_redeploys", self.text)
        self.assertIn("redeploy-${component}.yml", self.text)

    def test_the_redeploy_wait_counts_queued_runs_too(self):
        """Each redeploy has its own concurrency group, so one can sit queued.

        autopush's redeploys start on every push to main. A queued one that
        dequeues halfway through the apply is the collision the wait exists to
        avoid, and an `--status in_progress` query cannot see it.
        """
        self.assertIn('select(.status == "in_progress"', self.text)
        self.assertIn('"queued"', self.text)
        self.assertIn('"waiting"', self.text)

    def test_credentials_are_fetched_before_the_lease_is_taken(self):
        """The lease is a ConfigMap read; without a kubeconfig it cannot be read.

        `acquire` exits non-zero either way, so credentials fetched after it
        would make every scheduled reconcile defer, exit 0, and report green
        having applied nothing — the "green means deployed" failure this whole
        change exists to end.
        """
        # The invocation, not the first mention: the header comment names the
        # script several paragraphs above the code that runs it.
        creds = self.text.index("gcloud container clusters get-credentials")
        lease = self.text.index('live_test_lease.py" acquire')
        self.assertLess(creds, lease,
                        "get-credentials has to come before the lease is taken")

    def test_an_unreadable_lease_fails_rather_than_deferring(self):
        """A cluster that could not be asked has not answered "no"."""
        self.assertIn('grep -q \'"state": "held"\'', self.text)
        self.assertIn("Live-test lease could not be read", self.text)

    def test_an_apply_refuses_to_un_provision_the_minter(self):
        """enable_github_minter flips to false on a key with no ENABLED version.

        No variable expresses it, so REQUIRED_STRICT cannot cover it, and the
        installer's only signal is a warning in a log nobody reads.
        """
        self.assertIn("state=ENABLED", self.text)
        self.assertIn("DESTROY the minter", self.text)

    def test_a_failed_plan_does_not_report_itself_as_planned(self):
        """`result` is the caller's contract; only `drift` carried this before."""
        self.assertIn('output "result" "failed"', self.text)


class UpgradePlanFlagTest(unittest.TestCase):
    def setUp(self):
        self.text = _UPGRADE_SH.read_text()

    def test_plan_and_dry_run_cannot_be_combined(self):
        """They answer different questions; silently picking one would mislead."""
        self.assertIn(
            '--dry-run and --plan are different previews and cannot be combined',
            self.text)

    def test_an_empty_image_tag_is_still_an_error_for_an_upgrade(self):
        """Empty is the shape of a CI variable that did not resolve."""
        self.assertIn("--keep-image-tag", self.text)
        self.assertIn(
            '[ -z "$PARAM_IMAGE_TAG" ] && [ "$PARAM_KEEP_IMAGE_TAG" = "true" ]',
            self.text)

    def test_a_plan_skips_the_session_key_backfill(self):
        """backfill_session_kv_keys patches the live Secret."""
        self.assertIn(
            "Plan mode: skipping the Session KV backfill", self.text)


class ReportDriftTest(unittest.TestCase):
    def setUp(self):
        self.module = _load_report_drift()

    def test_it_summarises_a_plan_with_changes(self):
        plan = (
            "Terraform will perform the following actions:\n"
            "\n"
            "  # module.chat_pubsub[0].google_pubsub_topic.chat_events will be created\n"
            "  + resource \"google_pubsub_topic\" \"chat_events\" {\n"
            "    }\n"
            "\n"
            "  # module.kube_agents_iam.google_project_iam_member.agent will be updated in-place\n"
            "\n"
            "Plan: 1 to add, 1 to change, 0 to destroy.\n"
        )
        totals, actions, destructive = self.module.summarise(plan)
        self.assertEqual(totals, "Plan: 1 to add, 1 to change, 0 to destroy.")
        self.assertEqual(len(actions), 2)
        self.assertFalse(destructive)

    def test_a_destroy_is_called_out(self):
        """It is #1060's signature, and usually means missing configuration."""
        plan = (
            "  # module.gke_cluster.google_container_node_pool.gvisor[0] will be destroyed\n"
            "Plan: 0 to add, 0 to change, 1 to destroy.\n"
        )
        _, _, destructive = self.module.summarise(plan)
        self.assertTrue(destructive)
        body = self.module.body_for("autopush", "http://run", "", [], True)
        self.assertIn("[!WARNING]", body)

    def test_the_body_carries_a_marker_so_one_issue_is_reused(self):
        """A nightly that opens a new issue every night teaches everyone to mute it."""
        body = self.module.body_for("autopush", "http://run", "", [], False)
        self.assertIn(self.module.MARKER_TEMPLATE.format(env="autopush"), body)

    def test_a_tainted_replacement_is_not_dropped(self):
        """Terraform announces those as `is tainted, so must be replaced`.

        Missed by the regex, a plan whose only change is a tainted replacement
        renders an issue with no resource list and no destroy warning — the
        opposite of what it is reporting.
        """
        plan = (
            "  # module.gke_cluster.google_container_cluster.autopilot[0] is tainted,"
            " so must be replaced\n"
            "Plan: 1 to add, 0 to change, 1 to destroy.\n"
        )
        _, actions, destructive = self.module.summarise(plan)
        self.assertEqual(len(actions), 1)
        self.assertTrue(destructive)

    def test_the_label_is_created_before_the_first_issue(self):
        """`gh issue create --label` fails outright on a label that does not exist."""
        source = (_REPO_ROOT / "scripts" / "release" / "report_drift.py").read_text()
        self.assertIn("gh\", \"label\", \"create\"", source)
        create = source.index('"issue", "create"')
        ensure = source.index("ensure_label(args.repo)")
        self.assertLess(ensure, create)

    def test_an_empty_plan_yields_no_actions(self):
        totals, actions, destructive = self.module.summarise(
            "No changes. Your infrastructure matches the configuration.\n")
        self.assertEqual(totals, "")
        self.assertEqual(actions, [])
        self.assertFalse(destructive)


if __name__ == "__main__":
    unittest.main()
