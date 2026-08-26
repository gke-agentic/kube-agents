"""Unit tests for lifecycle.sh's Pub/Sub adoption and its usage block.

Nothing else covers this script. CI's shellcheck step reads only install.sh,
uninstall.sh and upgrade.sh, and there is no Terraform harness here, so the two
properties asserted below would regress silently.

Both are properties a reader cannot check by eye:

`adopt_pubsub` must not run from `apply`. A Chat topic is claimed by name, the
default name is a project-wide constant, and this composition supports two
installs in one project — so an automatic adoption can take over a live install's
topic, redirect its Chat traffic, and delete it on the next destroy. The 409 that
a bare `apply` produces instead is loud and destroys nothing. Wiring adoption back
into `apply` is a one-line change that looks like a tidy-up, which is exactly why
it is pinned here.

The usage block must print the whole header. It replaced a hard-coded `sed` line
range that was calibrated to the header's length, and adding a subcommand above it
silently truncated the help at the remote-state paragraph — the only place the
script documents KUBE_AGENTS_STATE_BUCKET.
"""

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_LIFECYCLE_SH = _REPO_ROOT / "terraform" / "examples" / "full-install" / "lifecycle.sh"


def _case_body(name: str) -> str:
    """Return the body of one `case` branch in the subcommand dispatch."""
    source = _LIFECYCLE_SH.read_text()
    match = re.search(rf"^  {re.escape(name)}\)\n(.*?)^    ;;$", source, re.S | re.M)
    assert match, f"no {name}) branch found in lifecycle.sh"
    return match.group(1)


class AdoptPubsubIsNotAutomatic(unittest.TestCase):
    def test_apply_does_not_call_adopt_pubsub(self):
        body = _case_body("apply")
        self.assertIn("adopt_kms", body, "apply should still adopt undeletable KMS resources")
        self.assertNotIn(
            "adopt_pubsub",
            body,
            "apply must not adopt Pub/Sub: the default topic name is a project-wide "
            "constant, so an automatic adoption can claim a second install's live "
            "topic and delete it on the next destroy. Keep the 409.",
        )

    def test_adopt_pubsub_is_reachable_as_its_own_subcommand(self):
        self.assertIn("adopt_pubsub", _case_body("adopt-pubsub"))


_TERRAFORM_STUB = """#!/usr/bin/env bash
case "$1" in
  init) exit 0 ;;
  console)
    read -r expr
    case "$expr" in
      var.enable_google_chat)      echo 'true' ;;
      var.project_id)              echo '"test-project"' ;;
      var.chat_topic_name)         echo '"platform-agent-chat-events"' ;;
      var.chat_subscription_name)  echo '"platform-agent-chat-events-sub"' ;;
      *)                           echo '""' ;;
    esac
    ;;
  state)  : ;;                       # `state list` -> empty, nothing is adopted yet
  import) printf '%s\\n' "$*" >>"$IMPORT_LOG" ;;
esac
exit 0
"""

# $ATTACHED_TOPIC is what `subscriptions describe --format=value(topic)` reports, which
# is the only input the attachment guard reads.
_GCLOUD_STUB = """#!/usr/bin/env bash
case "$*" in
  *"pubsub topics describe"*|*"pubsub subscriptions describe"*)
    if [ -n "${GCLOUD_DESCRIBE_ERR:-}" ]; then
      printf '%s\\n' "$GCLOUD_DESCRIBE_ERR" >&2
      exit 1
    fi
    ;;
esac
case "$*" in
  *"pubsub topics describe"*)        exit 0 ;;
  *"pubsub subscriptions describe"*) printf '%s\\n' "$ATTACHED_TOPIC"; exit 0 ;;
esac
exit 0
"""

# The two shapes Pub/Sub actually returns, both carrying NOT_FOUND. Keying the quiet
# path on NOT_FOUND therefore silences a wrong or inaccessible project as well as a
# genuine absence, which is the failure these two tests exist to keep fixed.
_ERR_NO_PROJECT = (
    "ERROR: (gcloud.pubsub.topics.describe) NOT_FOUND: Requested project not found "
    "or user does not have access to it (project=nope-zz123)."
)
_ERR_NO_RESOURCE = (
    "ERROR: (gcloud.pubsub.topics.describe) NOT_FOUND: Resource not found "
    "(resource=platform-agent-chat-events)."
)


class AttachmentGuard(unittest.TestCase):
    """Drive adopt_pubsub with gcloud and terraform stubbed, and watch what it imports.

    The guard is the safety property of the whole subcommand: it is what stops a
    same-named subscription that belongs to a different install being imported into
    this state, where the next destroy would delete it. Asserting that the `[[ ... ]]`
    test appears in the file does not constrain that — the string can be present and
    gate nothing — so this runs the code and asserts on the imports it performs.
    """

    def _run(self, attached_topic, describe_err=""):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        bindir = pathlib.Path(tmp) / "bin"
        bindir.mkdir()
        for name, body in (("terraform", _TERRAFORM_STUB), ("gcloud", _GCLOUD_STUB)):
            p = bindir / name
            p.write_text(body)
            p.chmod(0o755)

        # Copy the script out of the tree: it cd's to its own directory and writes a
        # provider override there while importing.
        script = pathlib.Path(tmp) / "lifecycle.sh"
        script.write_text(_LIFECYCLE_SH.read_text())
        script.chmod(0o755)

        import_log = pathlib.Path(tmp) / "imports.txt"
        import_log.touch()
        env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "IMPORT_LOG": str(import_log),
            "ATTACHED_TOPIC": attached_topic,
            "GCLOUD_DESCRIBE_ERR": describe_err,
        }
        env.pop("KUBE_AGENTS_STATE_BUCKET", None)
        result = subprocess.run(
            ["bash", str(script), "adopt-pubsub"],
            capture_output=True, text=True, env=env, check=False,
        )
        return result, import_log.read_text()

    def test_subscription_attached_to_a_foreign_topic_is_not_imported(self):
        result, imports = self._run("projects/test-project/topics/someone-elses-topic")
        combined = result.stdout + result.stderr

        self.assertNotIn(
            "google_pubsub_subscription", imports,
            "a subscription attached to another topic must never be imported — it "
            "belongs to something else, and this state's next destroy would delete it",
        )
        self.assertIn("leaving it alone", combined)
        # The topic itself is a legitimate adoption, which also proves the run got far
        # enough to have imported the subscription had the guard not stopped it.
        self.assertIn("google_pubsub_topic", imports)

    def test_subscription_attached_to_the_adopted_topic_is_imported(self):
        _, imports = self._run("projects/test-project/topics/platform-agent-chat-events")
        self.assertIn(
            "google_pubsub_subscription", imports,
            "the matching case must still adopt, or the guard is just a refusal",
        )

    def test_an_inaccessible_project_warns_rather_than_reporting_nothing_to_adopt(self):
        result, _ = self._run("", describe_err=_ERR_NO_PROJECT)
        combined = result.stdout + result.stderr
        self.assertIn(
            "could not read", combined,
            "a wrong or inaccessible project carries NOT_FOUND just like a genuine "
            "absence does, so keying the quiet path on NOT_FOUND reports an auth or "
            "project problem as '0 imported' — the one answer that cannot be true here",
        )

    def test_a_genuinely_absent_resource_stays_quiet(self):
        result, _ = self._run("", describe_err=_ERR_NO_RESOURCE)
        combined = result.stdout + result.stderr
        self.assertNotIn(
            "could not read", combined,
            "nothing to adopt is the expected case and must not warn",
        )


class UsageBlockIsComplete(unittest.TestCase):
    def test_usage_prints_the_whole_header(self):
        result = subprocess.run(
            ["bash", str(_LIFECYCLE_SH), "definitely-not-a-subcommand"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        for expected in (
            "./lifecycle.sh apply",
            "./lifecycle.sh adopt-kms",
            "./lifecycle.sh adopt-pubsub",
            # The last paragraph of the header. A range-based usage block that has
            # fallen behind the header drops this first.
            "KUBE_AGENTS_STATE_BUCKET",
            "KUBE_AGENTS_STATE_PREFIX",
        ):
            self.assertIn(expected, result.stdout, f"usage output is missing {expected!r}")

    def test_usage_does_not_depend_on_the_caller_working_directory(self):
        # BASH_SOURCE is whatever path the caller used, and the script cd's to its
        # own directory before the usage block reads the file back.
        result = subprocess.run(
            ["bash", str(_LIFECYCLE_SH.relative_to(_REPO_ROOT)), "nope"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn("KUBE_AGENTS_STATE_BUCKET", result.stdout)
        self.assertNotIn("can't open file", result.stderr)


if __name__ == "__main__":
    unittest.main()
