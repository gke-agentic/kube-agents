"""The agent deploy must prove the tag it set reached the pod template.

`helm upgrade --set platformAgent.deployment.image.tag=<sha>` is the only thing
the redeploy workflow changes, and the operator is free to ignore it:
resolveAgentImage (k8s-operator/internal/controller/manifest_helpers.go) reads
spec.deployment.tag only when spec.deployment.image carries no tag or digest of
its own. A `kubectl patch` pinning a full reference decides the image instead,
and it survives: the chart renders the bare repository identically every
release, so that field is absent from the patch Helm computes and the live
value is never touched.

Nothing downstream caught it. With the resolved image unchanged the operator
writes no new pod template, so `kubectl rollout status` returns success against
a ReplicaSet that was already complete. Autopush served an agent image nine
days old while every deploy in that window reported success.

Three things are pinned here. That the workflow runs the read-back before the
rollout gate. That the read-back covers every release image in the template,
because the credential-proxy sidecar is derived separately from the agent's own
reference and the two can come apart. And that it identifies those images the
way images.json does rather than by registry prefix -- a single-prefix mirror
puts fluent-bit alongside the release images, and a prefix rule would demand
the deploy's tag of a third-party pin and red a healthy deploy.
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

_GHCR = "ghcr.io/gke-labs/kube-agents"
_MIRROR = "europe-docker.pkg.dev/acme/mirror"


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

    def _run(self, *templates, tag=_TAG, timeout="0", interval="0"):
        """Run the script against one or more stubbed reads of the template.

        Each `name=image` listing answers one `kubectl get deployment` call, the
        last repeating once they run out, so a sequence exercises the poll. The
        CR reads on the failure path are answered separately and do not consume
        one.

        A zero timeout makes the failure paths immediate: the loop checks for
        success before it checks the deadline, so the passing cases are
        unaffected. Tests that need the loop to go round pass their own.
        """
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        stub_dir = pathlib.Path(holder.name)

        for index, template in enumerate(templates):
            (stub_dir / f"read-{index}").write_text(textwrap.dedent(template).strip() + "\n")

        kubectl = stub_dir / "kubectl"
        kubectl.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                if [[ "$*" == *platformagent* ]]; then
                  echo '  platform-agent: {_GHCR}/platform-agent:{_OLD} tag={_TAG}'
                  exit 0
                fi
                count_file="{stub_dir}/calls"
                count=$(cat "$count_file" 2>/dev/null || echo 0)
                echo $((count + 1)) >"$count_file"
                last={len(templates) - 1}
                [ "$count" -gt "$last" ] && count="$last"
                cat "{stub_dir}/read-${{count}}"
                """
            )
        )
        kubectl.chmod(0o755)

        env = dict(os.environ)
        env["PATH"] = f"{stub_dir}:{env['PATH']}"
        env["AGENT_IMAGE_CONFIRM_TIMEOUT"] = timeout
        env["AGENT_IMAGE_CONFIRM_INTERVAL"] = interval
        result = subprocess.run(
            [str(_SCRIPT), "kubeagents-system", _GATEWAY, tag],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        calls_file = stub_dir / "calls"
        self.calls = int(calls_file.read_text()) if calls_file.exists() else 0
        return result

    def test_it_passes_when_every_release_image_carries_the_tag(self):
        result = self._run(
            f"""
            sandbox-credential-cleanup={_GHCR}/platform-agent:{_TAG}
            envoy-credential-proxy={_GHCR}/credential-proxy:{_TAG}
            platform-agent={_GHCR}/platform-agent:{_TAG}
            fluent-bit=docker.io/fluent/fluent-bit:5.1.0
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("3 release image", result.stdout)

    def test_it_ignores_a_third_party_pin_on_its_own_registry(self):
        result = self._run(
            f"""
            platform-agent={_GHCR}/platform-agent:{_TAG}
            fluent-bit=docker.io/fluent/fluent-bit:5.1.0
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_it_ignores_a_third_party_pin_mirrored_under_the_release_prefix(self):
        # The shape a single-prefix mirror actually renders, and the one a
        # registry-prefix rule gets wrong. mirror_images.sh writes
        # <prefix>/<name> and the chart's thirdPartyImageRegistry falls back to
        # imageRegistry, so fluent-bit sits under the same prefix as the
        # release images while keeping its own upstream version. Demanding the
        # deploy's tag of it reds a healthy deploy and blames a CR pin that
        # does not exist.
        result = self._run(
            f"""
            sandbox-credential-cleanup={_MIRROR}/platform-agent:{_TAG}
            envoy-credential-proxy={_MIRROR}/credential-proxy:{_TAG}
            platform-agent={_MIRROR}/platform-agent:{_TAG}
            fluent-bit={_MIRROR}/fluent-bit:5.1.0
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("3 release image", result.stdout)

    def test_it_fails_when_the_agent_is_pinned_to_an_older_tag(self):
        # The incident: spec.deployment.image pinned to a full reference, so
        # the tag the deploy set was never consulted.
        result = self._run(
            f"""
            sandbox-credential-cleanup={_GHCR}/platform-agent:{_OLD}
            envoy-credential-proxy={_GHCR}/credential-proxy:{_OLD}
            platform-agent={_GHCR}/platform-agent:{_OLD}
            fluent-bit=docker.io/fluent/fluent-bit:5.1.0
            """
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(_OLD, result.stdout)
        self.assertIn("kubectl patch platformagent", result.stdout)

    def test_it_reports_the_operator_status_on_failure(self):
        # A pinned image is not the only way to get here: the operator returns
        # early on some conditions without re-rendering the pod template, and
        # the message must not assert the one cause it cannot distinguish.
        result = self._run(f"platform-agent={_GHCR}/platform-agent:{_OLD}")
        self.assertEqual(result.returncode, 1)
        self.assertIn("status:", result.stdout)

    def test_it_fails_on_a_sidecar_that_moved_without_the_agent(self):
        # The digest-pin path: the agent freezes at its digest while the
        # credential proxy beside it takes spec.deployment.tag and rolls
        # forward every deploy. Only a template-wide check reports which images
        # came apart, and the reverse skew is invisible without it.
        result = self._run(
            f"""
            platform-agent={_GHCR}/platform-agent:{_TAG}
            envoy-credential-proxy={_GHCR}/credential-proxy:{_OLD}
            fluent-bit=docker.io/fluent/fluent-bit:5.1.0
            """
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("envoy-credential-proxy", result.stdout)

    def test_it_fails_on_a_digest_pinned_agent(self):
        # A digest carries no tag, so the deploy's tag demonstrably did not
        # take effect however current the digest happens to be.
        result = self._run(f"platform-agent={_GHCR}/platform-agent@sha256:" + "0" * 64)
        self.assertEqual(result.returncode, 1)

    def test_it_fails_when_no_release_image_is_present(self):
        # An empty or unrecognisable read-back is not a pass. Before this the
        # deploy would have gone green on it.
        result = self._run("fluent-bit=docker.io/fluent/fluent-bit:5.1.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Found no first-party release image", result.stdout)

    def test_it_waits_out_a_template_the_operator_has_not_written_yet(self):
        # The reason the script is a loop. The operator reconciles
        # asynchronously and the deploy returns before it does, so a stale read
        # is the expected first answer -- giving up on it would red every real
        # deploy while accusing the CR of being pinned.
        result = self._run(
            f"platform-agent={_GHCR}/platform-agent:{_OLD}",
            f"platform-agent={_GHCR}/platform-agent:{_OLD}",
            f"platform-agent={_GHCR}/platform-agent:{_TAG}",
            timeout="30",
            interval="1",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls, 3, "expected the loop to poll until the template caught up")

    def test_it_waits_out_an_empty_read(self):
        # A Deployment the operator has not created yet reads back as nothing,
        # which is indistinguishable from a slow one this early.
        result = self._run(
            "",
            f"platform-agent={_GHCR}/platform-agent:{_TAG}",
            timeout="30",
            interval="1",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls, 2)


if __name__ == "__main__":
    unittest.main()
