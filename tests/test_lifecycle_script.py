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

import pathlib
import re
import subprocess
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

    def test_subscription_is_only_adopted_when_attached_to_the_adopted_topic(self):
        source = _LIFECYCLE_SH.read_text()
        self.assertIn(
            '[[ "$attached" == "projects/$project/topics/$topic" ]]',
            source,
            "the attachment guard is what stops a same-named subscription belonging "
            "to something else being imported into this state",
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
