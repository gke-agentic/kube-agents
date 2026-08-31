---
title: Release lifecycle, versioning & operations
description: How Kube-Agents automates SemVer 2.0 releases, validates release candidates on live GKE clusters, and publishes immutable artifacts.
sidebar:
  order: 4
---

`kube-agents` follows strict [Semantic Versioning 2.0.0](https://semver.org/) (`MAJOR.MINOR.PATCH`) without a `v` prefix for official releases across container images, OCI Helm charts, and Terraform modules.

The release pipeline guarantees that installer scripts (`install.sh`, `uninstall.sh`, `upgrade.sh`) and container runtime images are bit-for-bit synchronized from the exact same commit, validated on a live GKE cluster before any release tag is published.

## Tag and artifact taxonomy

Every commit and build progresses through four distinct lifecycle tiers:

| Tier | Format | Trigger | Purpose and guarantees |
| :--- | :--- | :--- | :--- |
| **Candidate Build** | `sha-<SHORT_SHA>` | Push to `main` branch | Developer build in GHCR; container images built once. |
| **Release Candidate (RC)** | `rc_YYMMDDHHMM_<SHORT_SHA>` | Push to `main` / 3-hour cron | Candidate build selected for live cluster testing. |
| **RC Validated** | `rc_YYMMDDHHMM_<SHORT_SHA>_validated` | Successful GKE E2E suite | Quality gate: proof that `install.sh` succeeded on a real GKE cluster. |
| **GA Stable** | `X.Y.Z` (pure numeric SemVer) | Release publish workflow | Official production release tagged on the validated commit. |

## Automated SemVer 2.0 calculation

When the GA release workflow runs, `scripts/release/calculate_next_version.sh` inspects Conventional Commits in the range `<LATEST_GA_TAG>..HEAD`:

<!-- prettier-ignore -->
| Commit type landed on `main` | Current version | Calculated next version | Precedence and action |
| :--- | :--- | :--- | :--- |
| `fix:`, `chore:`, `docs:`, `perf:` | `0.2.0` | `0.2.1` | Patch bump |
| `feat:` | `0.2.0` | `0.3.0` | Minor bump, Patch resets to 0 |
| `feat!:`, `fix!:`, `BREAKING CHANGE:` | `0.2.0` | `0.3.0` | Minor bump (SemVer 2.0 Clause 4 in `0.y.z`) |
| `feat!:`, `fix!:`, `BREAKING CHANGE:` | `1.2.0` | `2.0.0` | Major bump (in `1.x.x`+) |
| _(No new commits on main)_ | `0.2.0` | `0.2.0` | No changes (`skip_release=true`) |

### SemVer 2.0 Clause 4 and the 1.0.0 manual governance rule

1. **Initial development phase (`0.y.z`)**: Per [SemVer 2.0 Clause 4](https://semver.org/#spec-item-4), any breaking change during initial development increments `MINOR` (`0.2.1` -> `0.3.0`), resetting `PATCH` to 0.
2. **Manual `1.0.0` transition**: The automated version calculator **never** promotes `0.y.z` to `1.0.0` on its own. Declaring API stability and graduating to `1.0.0` is a manual governance decision by project maintainers. It must be triggered explicitly via the `explicit_release_version: "1.0.0"` input parameter.
3. **Stable phase (`1.x.x` and above)**: Once `1.0.0` is established, the automated calculator resumes normal SemVer rules: breaking changes bump `MAJOR`, new features bump `MINOR`, and bug fixes bump `PATCH`.

## Cutting a GA release

### Prerequisites

Before triggering a production release:
1. Target commit must exist on the `main` branch.
2. Target commit must carry an `rc_*_validated` tag created by the automated RC validation pipeline ([`scripts/release/README.md`](https://github.com/gke-labs/kube-agents/tree/main/scripts/release)).
3. All four required container images (`k8s-operator`, `platform-agent`, `credential-proxy`, `replay-proxy`) must exist in GHCR under `sha-<TARGET_COMMIT>`.

### Triggering the release workflow

Execute `.github/workflows/release-publish.yml` from the GitHub Actions web interface or via the GitHub CLI:

```bash
# Standard automated release (SemVer calculated automatically from Conventional Commits):
gh workflow run release-publish.yml --repo gke-labs/kube-agents

# Releasing a specific validated commit:
gh workflow run release-publish.yml --repo gke-labs/kube-agents \
  -f target_commit="d3be984d4128f73111f1816e138a06e938927909"

# Manual version override (e.g. promoting 0.y.z to 1.0.0):
gh workflow run release-publish.yml --repo gke-labs/kube-agents \
  -f explicit_release_version="1.0.0"
```

## Clean promotion and artifact guarantees

The release publish workflow enforces byte-for-byte fidelity with tested candidate binaries:

1. **Zero container rebuilds**: Container images are compiled only once on push to `main`. `scripts/release/promote_release_images.sh` retags existing `sha-<TARGET_COMMIT>` manifests to numeric `X.Y.Z` in GHCR using `docker buildx imagetools create`.
2. **Cosign OIDC signature**: Promoted container images in GHCR are cryptographically signed using Keyless Cosign via GitHub Actions OIDC tokens (`scripts/release/sign_release_images.sh`).
3. **OCI Helm chart**: `scripts/release/publish_helm_chart.sh` packages `charts/kube-agents` at version `X.Y.Z` (matching `appVersion`), pushes the OCI package to `oci://ghcr.io/gke-labs/kube-agents/charts/kube-agents:X.Y.Z`, and signs the OCI manifest via Cosign.
4. **Installer version lock**: `scripts/release/tag_ga_release.sh` creates a single-parent release commit on detached HEAD, stamps `BAKED_RELEASE_VERSION="X.Y.Z"` into root scripts (`install.sh`, `uninstall.sh`, `upgrade.sh`), and tags the stamped commit.
5. **Drift prevention in `install.sh`**: `verify_local_source_ref` verifies that unversioned source directories match `BAKED_RELEASE_VERSION` and that Git checkouts match the requested tag commit SHA, halting execution if local scripts diverge from container images.

## Helm chart versioning

The chart `version` tracks the application `appVersion`: the release workflow packages the
chart with both `version` and `appVersion` set to the exact SemVer release tag `X.Y.Z`, so every
chart release corresponds to exactly one application release. There is no chart-only release
train — a chart-template fix ships with the next `X.Y.Z` tag.

## Pinning Terraform module versions in GitOps

When configuring GitOps repositories, pin companion Terraform modules using the exact SemVer Git tag:

```hcl
module "gke_cluster" {
  source       = "git::https://github.com/gke-labs/kube-agents.git//terraform/modules/gke-cluster?ref=0.3.0"
  project_id   = var.project_id
  cluster_name = "production-host-01"
  location     = "us-central1"
}
```
