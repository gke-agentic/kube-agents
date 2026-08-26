# Release Candidate Automation Scripts

This directory contains executable scripts supporting the Release Candidate (RC) end-to-end automation pipeline.

## Overview of Scripts

- `common.sh`: Centralized registry/repository helpers (`DEFAULT_REGISTRY_PREFIX`, `DEFAULT_RELEASE_REPO`, `REQUIRED_RELEASE_IMAGES`), commit discovery (`find_latest_built_commit`), validation check (`is_commit_already_validated`), staging promotion tags (`STAGING_TAG_PREFIX`, `staging_tag_for_rc`, `get_existing_staging_tag`), container image promotion (`promote_release_images`), and automated bot tagging (`ensure_git_tag`).
- `resolve_rc_tag.sh`: Validates candidate commit SHAs, resolves input tags/commit inputs, discovers the latest built commit on `main` during scheduled runs, checks for existing `*_validated` tags to skip redundant runs, and sets workflow step outputs.
- `verify_candidate_images.sh`: Verifies that prebuilt container images (`k8s-operator`, `platform-agent`, `credential-proxy`, `replay-proxy`) exist in GHCR/registry for the target candidate SHA.
- `create_release_tag.sh`: Creates and pushes candidate release tags (`rc_YYMMDDHHMM_<short_sha>`, derived from commit timestamp) safely and idempotently. When executed locally outside CI, runs in dry-run mode (creates tag locally and skips remote push).
- `validate_and_log_deploy_summary.sh`: Validates required environment variables and secrets, then logs a formatted deployment matrix and GCP cluster target overview for auditing before provisioning.
- `provision_rc_environment.sh`: Orchestrates cluster teardown and fresh provisioning against the dedicated RC GCP project.
- `tag_validated_release.sh`: Attaches the `*_validated` tag to a candidate commit upon 100% test pass.
- `resolve_promotion_candidate.sh`: Selects the validated candidate the nightly staging promotion should test — latest `rc_*_validated` by default, or an explicit tag — rejects a commit carrying no `*_validated` tag, derives the `staging/<rc_tag>` promotion tag, and reports `skip_promotion=true` when a `staging/**` tag already points at that commit.
- `tag_staging_promotion.sh`: Pushes the `staging/**` tag that deploys a validated candidate to the staging environment. The push is the deploy trigger, so the calling job must check out with `RELEASE_BOT_TOKEN`: a tag pushed with the default `GITHUB_TOKEN` starts no workflow.
- `calculate_next_version.sh`: Automatically calculates the next SemVer 2.0 version from Conventional Commits since the latest numeric GA release tag.
- `verify_release_eligibility.sh`: Release gatekeeper that verifies commit eligibility, checks for live RC validation tags (`rc_*_validated`), performs tag collision detection, and verifies all 4 required container images exist in registry.
- `tag_ga_release.sh`: Creates and pushes official GA SemVer Git tags (`X.Y.Z`) directly on the validated commit SHA.
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

The staging promotion pipeline (`.github/workflows/staging-promote.yml`) runs on top of that output,
nightly at 02:00 UTC: it resolves the newest `rc_*_validated` candidate, redeploys that exact commit
to the RC environment, runs the full `nightly-e2e` matrix against it, and pushes `staging/<rc_tag>`
only if the matrix passes. `docs/designs/e2e-testing-harness.md` is the canonical description of the
gate and of why the candidate is redeployed rather than tested where it sits.

## Workflow Mapping

These modular scripts back the corresponding child workflows in `.github/workflows/`:

| GitHub Workflow                                  | Release Step                            | Executed Scripts                                                                                                                                                                               |
| ------------------------------------------------ | --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `rc-create-tag.yml`                              | Step 1 - Create Candidate Tag           | `resolve_rc_tag.sh`, `verify_candidate_images.sh`, `create_release_tag.sh`                                                                                                                     |
| `rc-deploy-environment.yml`                      | Step 2 - Deploy Environment             | `resolve_rc_tag.sh`, `validate_and_log_deploy_summary.sh`, `provision_rc_environment.sh`                                                                                                       |
| `e2e-gchat-test.yml` / `rc-release-pipeline.yml` | Step 3 - GKE Readiness & E2E Validation | `install_e2e_deps.sh`, `wait_for_gke_readiness.sh`, `execute_e2e_tests.sh`                                                                                                                     |
| `rc-tag-validated.yml`                           | Step 4 - Validate Candidate Commit      | `resolve_rc_tag.sh`, `tag_validated_release.sh`                                                                                                                                                |
| `staging-promote.yml`                            | Nightly Staging Promotion               | `resolve_promotion_candidate.sh`, `verify_candidate_images.sh`, `tag_staging_promotion.sh`                                                                                                     |
| `release-publish.yml`                            | GA Release Orchestration                | `calculate_next_version.sh`, `verify_release_eligibility.sh`, `promote_release_images.sh`, `sign_release_images.sh`, `tag_ga_release.sh`, `publish_helm_chart.sh`, `publish_github_release.sh` |
