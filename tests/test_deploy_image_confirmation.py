"""The agent deploy must prove the tag it set reached the pod template.

`helm upgrade --set platformAgent.deployment.image.tag=<sha>` is the only thing
the redeploy workflow changes, and the operator is free to ignore it:
resolveAgentImage (k8s-operator/internal/controller/manifest_helpers.go) reads
spec.deployment.tag only when spec.deployment.image carries no tag or digest of
its own. A `kubectl patch` pinning a full reference outranks every later deploy
for as long as it sits there.

Nothing downstream caught it. With the resolved image unchanged the operator
writes no new pod template, so `kubectl rollout status` returns success against
a ReplicaSet that was already complete. Autopush served a nine-day-old agent
through nine consecutive green deploys that way.

Two things are pinned here: that the workflow runs the read-back before the
rollout gate, and that the read-back covers every release image in the
template. The second matters because three of the gateway's four images move
with the deploy and one of them -- the credential-proxy sidecar -- is derived
separately, so they can come apart.
"""

import os
import pathlib
import subprocess
import tempfile
import textwrap
import unittest

import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_AGENT_WORKFLOW = _ROOT / ".github" / "workflows" / "reusable-deploy-agent.yml"
_SCRIPT = _ROOT / "scripts" / "confirm_agent_image.sh"

_GATEWAY = "platform-agent-gateway"
_TAG = "f1908801e545abffd967d3b8bf34d58833f5d945"
_OLD = "a1456a4b0b5b60090b96bd70edb030a53873768d"


class DeployWorkflowWiringTest(unittest.TestCase):
    """Where the read-back sits in the deploy job."""

    def setUp(self):
        steps = yaml.safe_load(_AGENT_WORKFLOW.read_text())["jobs"]["deploy"]["steps"]
        self.runs = [step.get("run", "") for step in steps]

    def _index_of(self, needle, description):
        for index, run in enumerate(self.runs):
            if needle in run:
                return index
        self.fail(f"no step in {_AGENT_WORKFLOW.name} {description}")

    def test_the_deploy_confirms_the_tag_it_set(self):
        self._index_of(_SCRIPT.name, f"runs {_SCRIPT.name}")

    def test_the_confirmation_precedes_the_rollout_gate(self):
        # Ordering is not cosmetic. The gate is the assertion that cannot tell
        # a fresh ReplicaSet from an untouched one, so a deploy the operator
        # ignored reaches it already looking successful.
        confirm = self._index_of(_SCRIPT.name, f"runs {_SCRIPT.name}")
        gate = self._index_of(
            f"rollout status deployment/{_GATEWAY}",
            f"runs `kubectl rollout status` on {_GATEWAY}",
        )
        self.assertLess(
            confirm,
            gate,
            "the tag confirmation must run before `kubectl rollout status`",
        )


class ConfirmAgentImageScriptTest(unittest.TestCase):
    """What the script accepts and what it refuses.

    Driven through a stub `kubectl` on PATH, so these are the script's real
    control flow rather than a transcription of it.
    """

    def _run(self, template, tag=_TAG, timeout="0"):
        """Run the script against a stubbed pod template.

        `template` is the name=image listing the real jsonpath produces. A zero
        timeout makes the failure paths immediate: the loop checks for success
        before it checks the deadline, so the passing cases are unaffected.
        """
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        stub_dir = pathlib.Path(holder.name)
        kubectl = stub_dir / "kubectl"
        kubectl.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                # The CR read on the failure path prints diagnostics only.
                if [[ "$*" == *platformagent* ]]; then
                  echo '{{"image":"ghcr.io/gke-labs/kube-agents/platform-agent:{_OLD}"}}'
                  exit 0
                fi
                cat <<'TEMPLATE'
                {textwrap.indent(template, "                ").strip()}
                TEMPLATE
                """
            )
        )
        kubectl.chmod(0o755)

        env = dict(os.environ)
        env["PATH"] = f"{stub_dir}:{env['PATH']}"
        env["AGENT_IMAGE_CONFIRM_TIMEOUT"] = timeout
        env["AGENT_IMAGE_CONFIRM_INTERVAL"] = "0"
        return subprocess.run(
            [str(_SCRIPT), "kubeagents-system", _GATEWAY, tag],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

    def test_it_passes_when_every_release_image_carries_the_tag(self):
        result = self._run(
            f"""
            sandbox-credential-cleanup=ghcr.io/gke-labs/kube-agents/platform-agent:{_TAG}
            envoy-credential-proxy=ghcr.io/gke-labs/kube-agents/credential-proxy:{_TAG}
            platform-agent=ghcr.io/gke-labs/kube-agents/platform-agent:{_TAG}
            fluent-bit=docker.io/fluent/fluent-bit:5.1.0
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("3 release image", result.stdout)

    def test_it_ignores_third_party_pins(self):
        # fluent-bit is pinned to its own version by images.json and must never
        # be expected to carry the deploy's tag.
        result = self._run(
            f"""
            platform-agent=ghcr.io/gke-labs/kube-agents/platform-agent:{_TAG}
            fluent-bit=docker.io/fluent/fluent-bit:5.1.0
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_it_follows_a_mirrored_registry(self):
        # An install that mirrors the images to its own registry rewrites the
        # repository, so the registry is not the deploy's to predict. The tag
        # is. Hardcoding ghcr.io here would red every mirrored install.
        result = self._run(
            f"""
            platform-agent=europe-docker.pkg.dev/acme/mirror/platform-agent:{_TAG}
            envoy-credential-proxy=europe-docker.pkg.dev/acme/mirror/credential-proxy:{_TAG}
            fluent-bit=docker.io/fluent/fluent-bit:5.1.0
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_it_fails_when_the_agent_is_pinned_to_an_older_tag(self):
        # The incident: spec.deployment.image pinned to a full reference, so
        # the tag the deploy set was never consulted.
        result = self._run(
            f"""
            sandbox-credential-cleanup=ghcr.io/gke-labs/kube-agents/platform-agent:{_OLD}
            envoy-credential-proxy=ghcr.io/gke-labs/kube-agents/credential-proxy:{_OLD}
            platform-agent=ghcr.io/gke-labs/kube-agents/platform-agent:{_OLD}
            fluent-bit=docker.io/fluent/fluent-bit:5.1.0
            """
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("platform-agent", result.stdout)
        self.assertIn(_OLD, result.stdout)
        self.assertIn("kubectl patch platformagent", result.stdout)

    def test_it_fails_on_a_sidecar_that_moved_without_the_agent(self):
        # The digest-pin path: the agent freezes at its digest while the
        # credential proxy beside it takes spec.deployment.tag and rolls
        # forward every deploy. Checking the agent container alone would still
        # catch this one, but only a template-wide check reports which images
        # actually came apart -- and the reverse skew, an agent that moved
        # while the sidecar did not, is invisible without it.
        result = self._run(
            f"""
            platform-agent=ghcr.io/gke-labs/kube-agents/platform-agent:{_TAG}
            envoy-credential-proxy=ghcr.io/gke-labs/kube-agents/credential-proxy:{_OLD}
            fluent-bit=docker.io/fluent/fluent-bit:5.1.0
            """
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("envoy-credential-proxy", result.stdout)

    def test_it_fails_on_a_digest_pinned_agent(self):
        # A digest carries no tag, so the deploy's tag demonstrably did not
        # take effect however current the digest happens to be.
        result = self._run(
            "platform-agent=ghcr.io/gke-labs/kube-agents/platform-agent@sha256:" + "0" * 64
        )
        self.assertEqual(result.returncode, 1)

    def test_it_fails_when_no_agent_image_is_present(self):
        # An empty or unrecognisable read-back is not a pass. Before this the
        # deploy would have gone green on it.
        result = self._run("fluent-bit=docker.io/fluent/fluent-bit:5.1.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Could not find a platform-agent image", result.stdout)


if __name__ == "__main__":
    unittest.main()
