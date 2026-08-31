# Release Candidate Automation Scripts

This directory contains executable scripts supporting the Release Candidate (RC) end-to-end automation pipeline.

## Release note: `PLATFORM_AGENT_PERMISSION_SET=gke-admin` now fails the deploy

**Action required before the next RC deploy** for any GitHub environment whose
`PLATFORM_AGENT_PERMISSION_SET` variable is set to `gke-admin`. That value has been removed and
`install.sh` now exits non-zero on it, so the deploy hard-fails rather than falling back to a
default.

**It does not fail before doing damage.** `provision_rc_environment.sh` is `uninstall.sh` followed
by `install.sh`, and the refusal fires while `install.sh` is collecting configuration — before
`terraform apply`, but after the teardown has already run. Expect a torn-down RC environment that
was not rebuilt, not a run that refused to start.

`rc-deploy-environment.yml` forwards `vars.PLATFORM_AGENT_PERMISSION_SET` verbatim to both
`validate_and_log_deploy_summary.sh` and `provision_rc_environment.sh`, so the summary step logs the
doomed value and proceeds. The refusal itself is fail-closed by design — `roles/container.admin`
authorizes the agent through IAM regardless of its Kubernetes RBAC, and its
`container.clusters.impersonate` permission cannot be scoped by IAM — but nothing warns you ahead of
the run.

Fix it by editing the environment variable to `read-only`, or to `custom` with
`PLATFORM_AGENT_CUSTOM_ROLES` naming every role, if you accept that risk explicitly. The reasoning
is on the site's [Security & IAM](../../docs/site/src/content/docs/reference/security-and-iam.md)
page under "Why there is no `gke-admin` set".

## Overview of Scripts

- `common.sh`: Centralized registry/repository helpers (`DEFAULT_REGISTRY_PREFIX`, `DEFAULT_RELEASE_REPO`, `REQUIRED_RELEASE_IMAGES`), commit discovery (`find_latest_built_commit`), validation check (`is_commit_already_validated`), container image promotion (`promote_release_images`), and automated bot tagging (`ensure_git_tag`). It also holds `release_resolve_target`, which resolves the cluster the two kubectl-facing scripts below act on. **In CI that resolution has no defaults**: `GKE_CLUSTER_NAME`, `GCP_REGION`, `GCP_PROJECT_ID` and `AGENT_NAMESPACE` must all be set — they come from the job's `env:` block, which reads them from the workflow's GitHub environment — and the script exits non-zero naming whichever is missing. A release script guessing which project it targets is the failure this prevents, since the old default pointed a teardown-and-reinstall at `kube-agents-rc` whatever the caller meant. Outside CI (`CI` unset or falsy) the developer defaults still apply, so running these by hand after `install.sh` needs no extra exports.
- `resolve_rc_tag.sh`: Validates candidate commit SHAs, resolves input tags/commit inputs, discovers the latest built commit on `main` during scheduled runs, checks for existing `*_validated` tags to skip redundant runs, and sets workflow step outputs.
- `verify_candidate_images.sh`: Verifies that prebuilt container images (`k8s-operator`, `platform-agent`, `credential-proxy`, `replay-proxy`) exist in GHCR/registry for the target candidate SHA.
- `create_release_tag.sh`: Creates and pushes candidate release tags (`rc_YYMMDDHHMM_<short_sha>`, derived from commit timestamp) safely and idempotently. When executed locally outside CI, runs in dry-run mode (creates tag locally and skips remote push).
- `validate_and_log_deploy_summary.sh`: Validates required environment variables and secrets, then logs a formatted deployment matrix and GCP cluster target overview for auditing before provisioning.
- `rc_teardown_common.sh`: Sourced by the two scripts below, which both call `uninstall.sh` and read the same three outcomes out of its exit code (`./uninstall.sh --help` lists them). Holds the invocation, the `RC_TEARDOWN_STRICT` parsing, and the job-summary rendering; each caller decides for itself what a failure means.
- `provision_rc_environment.sh`: Tears the RC environment down with `uninstall.sh`, then reinstalls it at the candidate commit with `install.sh`, against the dedicated RC GCP project. A failed teardown raises an `::error` annotation and a job-summary entry carrying the teardown output, and provisions anyway unless `RC_TEARDOWN_STRICT` is truthy — the choice between validating a candidate against stale state and letting a teardown problem block every release. It also forwards the GitOps repository and, with it, the GitHub token minter, and stages an optional `GH_APP_PRIVATE_KEY` to a private temporary file because `install.sh` takes a path; see "Enabling the GitHub token minter on the RC" below for what to set.
- `teardown_rc_environment.sh`: Destroys the RC environment after a run that passed end to end, so the cluster exists only for the length of a run rather than idling between the 3-hourly ones. A failure here is always fatal and `RC_TEARDOWN_STRICT` does not apply: nothing runs afterwards, so the alternative to a red job is a GKE cluster billing under a green pipeline. It runs only when steps 1–4 all succeeded, which is what leaves a failed run's environment standing to be examined live.
- `install_pubsub_platform.sh`: Installs `agentplugins/pubsub-platform`, the adapter that turns a Pub/Sub alert into agent work, and waits for the plugin to reconcile and the gateway's generation to settle. It exists because the adapter is a gateway singleton the agent image does not carry and the install engine does not deploy: the stockout investigator and any other alert producer contribute only route config, so without the adapter the gateway opens no listener and every alert-driven test fails on silence. That is a gap in the install rather than in the harness, tracked in [#1013](https://github.com/gke-labs/kube-agents/issues/1013); this makes the gate honest until that lands, and is meant to be deleted with it. Called by `rc-release-pipeline.yml`, which pays for a Helm release and, when the plugin source has changed since the last run, an image build. It exits non-zero when it cannot deliver working ingress and leaves the consequence to the caller: the RC pipeline's step is `continue-on-error` because alert ingress is a dependency of its optional suite alone. That covers the failures this script detects and reports, and no more — an adapter that installs cleanly and then wedges the gateway rollout fails `wait_for_gke_readiness.sh`, which carries no `continue-on-error`, and the Chat gate never runs. The script's own header states the limit; do not read the flag as a guarantee the mandatory gate is insulated. `SKIP_PUBSUB_PLATFORM` opts a run out. `e2e-nightly-matrix.yml` and `e2e-manual-runner.yml` should call it too and do not yet: neither binds to a GitHub environment, so their `vars.*` resolve against repository-level variables that do not exist and both fail at `get-gke-credentials`. Wiring them up is part of #1013's follow-up.
- `wait_for_gke_readiness.sh`: Connects `kubectl` to the target cluster, configures Artifact Registry credentials, optionally verifies the gateway is running the candidate commit's image, and waits for `litellm` and `platform-agent-gateway` to report ready. It waits and does not install. In `rc-release-pipeline.yml` — the one caller that installs alert ingress today — `install_pubsub_platform.sh` runs before it, so the gateway re-template the adapter causes is already in flight when the rollout waits start. Ordering the two is the caller's job, not something this script checks.
- `tag_validated_release.sh`: Attaches the `*_validated` tag to a candidate commit upon 100% test pass.
- `calculate_next_version.sh`: Automatically calculates the next SemVer 2.0 version from Conventional Commits since the latest numeric GA release tag.
- `verify_release_eligibility.sh`: Release gatekeeper that verifies commit eligibility, checks for live RC validation tags (`rc_*_validated`), performs tag collision detection, and verifies all 4 required container images exist in registry.
- `tag_ga_release.sh`: Creates and pushes official GA SemVer Git tags (`X.Y.Z`) on a detached HEAD commit stamped with the release version in installer scripts (`install.sh`, `uninstall.sh`, `upgrade.sh`). Note: candidate commits must carry the `^BAKED_RELEASE_VERSION=` placeholder line in root installer scripts.
- `promote_release_images.sh`: Promotes verified container images from candidate commit SHA to GA release tag in GHCR without rebuilding.
- `sign_release_images.sh`: Signs promoted GA release container images in GHCR using Keyless Cosign OIDC.
- `publish_helm_chart.sh`: Packages, publishes, and signs the official kube-agents Helm chart to GHCR as an OCI artifact.
- `publish_github_release.sh`: Publishes official GitHub Releases with auto-generated release notes from Conventional Commits.

## Pipeline Cadence & Execution Flow

The end-to-end pipeline (`.github/workflows/rc-release-pipeline.yml`) runs on a recurring schedule and can also be triggered manually:

- **Scheduled Cadence (every 3 hours `17 */3 * * *`, best-effort)**:
  - Automatically scans recent commits on `main` (`FETCH_HEAD`) for published container images in GHCR.
  - **Redundant Run Skipping**: If the latest candidate commit already carries a `*_validated` tag or was previously attempted, the pipeline skips subsequent provisioning and E2E test execution (`skip_rc=true`), finishing in seconds.
  - _Note_: Scheduled runs are scheduled at minute `17` to avoid GitHub Actions peak top-of-the-hour queue congestion; actual start times are best-effort based on GitHub scheduler availability.
- **Manual Trigger (`workflow_dispatch`)**:
  - Requires an explicit `commit_sha` input to rigorously test a specific target commit.

## What Happens to the RC Cluster

The pipeline builds a full GKE cluster per candidate and destroys it twice over: step 2 removes whatever was there before it installs, and step 5 removes what the run itself built. A run that passes therefore leaves nothing behind and nothing billing.

A run that fails anywhere does leave its environment standing, deliberately — step 5 hangs off the success of every earlier job, and the E2E failures worth diagnosing are the ones that only reproduce on the cluster that produced them. Two consequences to know about:

- Nothing else removes that environment. The next run's step 2 does, which on the schedule is up to three hours later, so an investigation that needs longer than that wants the schedule paused rather than a race against it.
- Step 2 is the only thing standing between a surviving environment and a candidate validated against stale state, which is what `RC_TEARDOWN_STRICT` decides. Truthy stops the run instead of installing on top; the same failure in step 5 is fatal regardless, because no later step compensates for it. Set it on the `rc` environment, where `GCP_PROJECT_ID` and every other value these jobs read already live — the repository level holds none of them, and `vars` resolving environment over repository makes a stray repository-level copy easy to set and then not find again.

## Enabling the GitHub token minter on the RC

`test_github_token_minting_and_connectivity` mints a real GitHub App token inside the agent pod and reads a repository back through it. It fails on an install where the minter was never provisioned: the chart renders the `github-token-minter` Deployment only under `githubMinter.enabled`, so the credential sidecar's refresh reaches no broker and answers `HTTP 502`, with the reason logged inside the sidecar where CI never sees it.

The repository it probes comes from the same two variables that scope the minter, so the two cannot drift: `rc-deploy-environment.yml` gives them to the installer and `rc-release-pipeline.yml` gives them to the suite. The GitHub App has to be installed on that repository — a token minted for one repository does not authenticate against another.

Three settings on the `rc` GitHub environment turn it on, and all three must be present before the minter is provisioned at all ([`installer_common.sh`](../../k8s-operator/scripts/installer_common.sh)). All three empty is a supported configuration — an install without a minter, which is the default everywhere outside the RC. Some set and some empty is not: `provision_rc_environment.sh` refuses, before the teardown, rather than reprovisioning an RC whose token-minting test would fail with an HTTP 502 forty minutes later.

`GH_APP_ID` is a _secret_, and that takes one thing the two variables do not. A called workflow receives only the secrets its caller passes, so reaching the `rc` environment's copy needs both halves: `rc-release-pipeline.yml` calling this workflow with `secrets: inherit`, and the `deploy-rc` job declaring `environment: rc`. An explicit `secrets:` mapping in the caller cannot substitute — a `uses:` job has no environment, so it resolves the names against nothing and forwards empty strings, which is indistinguishable from never having configured the minter. `tests/test_rc_minter_secret_wiring.py` pins both halves.

Set all three on the environment rather than the repository. A repository-level copy is not invisible — `vars` resolve environment over repository, and `secrets: inherit` carries the caller's repository secrets too — which is the problem: the wrong scope quietly works, so a stray copy is easy to set and then never find again.

| Setting                | Value                  | Notes                                                                                                 |
| ---------------------- | ---------------------- | ----------------------------------------------------------------------------------------------------- |
| Variable `GITOPS_ORG`  | `gke-agentic`          | Repository owner.                                                                                     |
| Variable `GITOPS_REPO` | `kube-agents-rc-infra` | Bare name, not `owner/repo`. Terraform's `github_repo` is composed as `${GITHUB_ORG}/${GITHUB_REPO}`. |
| Secret `GH_APP_ID`     | the App ID             | Same App that is installed on the repository above.                                                   |

`GITOPS_ORG` and `GITOPS_REPO` are deliberately separate from `GH_ORG` and `GH_REPO`, which every other workflow does use for this. On the `rc` environment that pair names the _release_ repository (`gke-labs/kube-agents`) and is what `common.sh`'s `get_target_repo` resolves for tag and release operations; pointing the minter at it would scope a live App token to this repository.

The App's private key is separate, because it is signing material rather than configuration and never enters Terraform state. Import it into the minter's KMS key once, by hand. [`terraform/modules/github-minter/README.md`](../../terraform/modules/github-minter/README.md) is canonical for that import and carries the Minty CLI route inline; it hands the `gcloud`/`openssl` path, for a host whose Go toolchain cannot build it, to `k8s-operator/config/integrations/github/README.md`. For the RC, the parameters it asks for are project `kube-agents-rc` and location `us-central1` (the KMS location is `GCP_REGION` with any zone suffix stripped, so it moves if the region does), with the default `github-token-minter-keyring` and `github-token-minter-key` names.

Do not hand-create the key from the `gcloud kms keys create` in that module's Terraform without `--skip-initial-version-creation`: KMS rejects an import-only key that does not skip it, which is why `skip_initial_version_creation = true` is set on the resource and why `install.sh`'s own pre-create passes the flag.

That import is a one-off. The key ring survives the teardown/reinstall cycle — `terraform destroy` cannot delete a Cloud KMS key ring, so `lifecycle.sh adopt-kms` re-adopts it on every apply — and both the enable decision and `install.sh`'s own import step short-circuit on an existing enabled version. Confirm with:

```bash
gcloud kms keys versions list --key=github-token-minter-key \
  --keyring=github-token-minter-keyring --location=us-central1 \
  --project=kube-agents-rc --filter=state=ENABLED
```

Setting an optional `GH_APP_PRIVATE_KEY` secret to the `.pem` contents is the alternative: `provision_rc_environment.sh` writes it to a private temporary file and hands `install.sh` the path, which imports it on the first install that finds no enabled version. It exists to bootstrap an environment without a manual step, and costs an App private key living in GitHub Actions — which is why the manual import is the better of the two.

## Workflow Mapping

These modular scripts back the corresponding child workflows in `.github/workflows/`:

| GitHub Workflow               | Release Step                            | Executed Scripts                                                                                                                                                                               |
| ----------------------------- | --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `rc-create-tag.yml`           | Step 1 - Create Candidate Tag           | `resolve_rc_tag.sh`, `verify_candidate_images.sh`, `create_release_tag.sh`                                                                                                                     |
| `rc-deploy-environment.yml`   | Step 2 - Deploy Environment             | `resolve_rc_tag.sh`, `validate_and_log_deploy_summary.sh`, `provision_rc_environment.sh`                                                                                                       |
| `rc-release-pipeline.yml`     | Step 3 - GKE Readiness & E2E Validation | `install_e2e_deps.sh`, `install_pubsub_platform.sh`, `wait_for_gke_readiness.sh`, `execute_e2e_tests.sh`                                                                                       |
| `rc-tag-validated.yml`        | Step 4 - Validate Candidate Commit      | `resolve_rc_tag.sh`, `tag_validated_release.sh`                                                                                                                                                |
| `rc-teardown-environment.yml` | Step 5 - Tear Down Environment          | `resolve_rc_tag.sh`, `teardown_rc_environment.sh`                                                                                                                                              |
| `release-publish.yml`         | GA Release Orchestration                | `calculate_next_version.sh`, `verify_release_eligibility.sh`, `tag_ga_release.sh`, `promote_release_images.sh`, `sign_release_images.sh`, `publish_helm_chart.sh`, `publish_github_release.sh` |
