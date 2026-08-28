"""How the RC pipeline delivers the token minter's environment secrets.

GH_APP_ID and GH_APP_PRIVATE_KEY live on the `rc` GitHub environment. Reaching
them from a called workflow takes two things at once, and having only one of
them fails silently:

  1. the called workflow's job declares `environment: rc`, and
  2. the calling job passes `secrets: inherit`.

An explicit `secrets:` mapping in the caller cannot substitute for (2). A
`uses:` job cannot declare an environment, so the mapping is evaluated in the
caller — where the environment secrets are not visible — and forwards empty
strings. The install then sees an empty GITHUB_APP_ID, skips the minter, and
the run dies later in test_github_token_minting_and_connectivity with an HTTP
502 that names nothing. That shipped once; these tests are so it cannot ship
again without a red build.
"""

import pathlib
import unittest

import yaml

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"
_PIPELINE = _WORKFLOWS / "rc-release-pipeline.yml"
_DEPLOY = _WORKFLOWS / "rc-deploy-environment.yml"
_PROVISION = _REPO_ROOT / "scripts" / "release" / "provision_rc_environment.sh"


def _jobs(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text())["jobs"]


class RcMinterSecretWiringTest(unittest.TestCase):
    def test_the_deploy_job_is_called_with_secrets_inherit(self):
        job = _jobs(_PIPELINE)["step-2-deploy-env"]
        self.assertEqual(
            job.get("secrets"),
            "inherit",
            "step-2-deploy-env must pass `secrets: inherit`. An explicit mapping is "
            "evaluated in this job, which has no `environment:`, so the rc "
            "environment's GH_APP_ID is forwarded empty and the minter is skipped.",
        )

    def test_the_called_job_declares_the_rc_environment(self):
        """The other half. Inheriting is useless if the job cannot see the environment."""
        self.assertEqual(_jobs(_DEPLOY)["deploy-rc"].get("environment"), "rc")

    def test_the_called_job_reads_both_app_secrets_from_the_environment(self):
        """Pins what the inherit is for, so a rename cannot quietly orphan it."""
        body = _DEPLOY.read_text()
        for secret in ("secrets.GH_APP_ID", "secrets.GH_APP_PRIVATE_KEY"):
            self.assertIn(secret, body)

    def test_a_half_configured_minter_stops_the_deploy(self):
        """Warning past this is what turned a missing secret into a 502 downstream."""
        body = _PROVISION.read_text()
        self.assertIn(
            "::error title=GitHub token minter is half-configured",
            body,
            "the partial-config branch must raise an ::error, not a ::warning",
        )
        self.assertNotIn(
            "::warning title=GitHub token minter not provisioned",
            body,
            "the old warning-and-continue behaviour is what this replaces",
        )
        # The `exit 1` has to be inside that branch, not merely somewhere in the file.
        branch = body.split('if [ -n "${GITHUB_MINTER_SET}" ] && [ -n "${GITHUB_MINTER_MISSING}" ]; then', 1)
        self.assertEqual(len(branch), 2, "the partial-config guard has moved or been renamed")
        self.assertIn(
            "exit 1",
            branch[1].split("\nfi\n", 1)[0],
            "the partial-config branch must exit non-zero",
        )

    def test_all_three_unset_is_still_allowed(self):
        """An install deliberately without the minter is the default everywhere else.

        The guard fires only when the set and missing lists are both non-empty, so
        it must not be rewritten as a bare "any of them missing" check.
        """
        self.assertIn(
            'if [ -n "${GITHUB_MINTER_SET}" ] && [ -n "${GITHUB_MINTER_MISSING}" ]; then',
            _PROVISION.read_text(),
        )


if __name__ == "__main__":
    unittest.main()
