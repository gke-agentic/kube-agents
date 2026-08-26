---
title: Release versioning & promotion
description: How Kube-Agents Release Candidate builds are promoted to immutable SemVer releases across container images, Helm charts, and Terraform modules.
sidebar:
  order: 4
---

`kube-agents` follows strict [Semantic Versioning 2.0.0](https://semver.org/) (`MAJOR.MINOR.PATCH`) for production releases across Docker images, OCI Helm charts, and Terraform modules.

## Promotion from Release Candidate (RC) to staging

Staging tracks the newest release candidate that has passed the full nightly E2E matrix. The
promotion is automatic: `staging-promote.yml` runs at 02:00 UTC, deploys the newest
`rc_*_validated` candidate to the RC cluster, runs the matrix against that exact commit, and
pushes a `staging/<rc_tag>` Git tag only if it passes. The three `staging-redeploy-*.yml`
workflows deploy from that tag. A failed matrix pushes nothing, so staging stays on the last
candidate that passed. Pushing a `staging/**` tag by hand still works and still deploys —
[`docs/designs/e2e-testing-harness.md`](https://github.com/gke-labs/kube-agents/blob/main/docs/designs/e2e-testing-harness.md)
is the canonical description of the gate.

## Promotion from Release Candidate (RC) to SemVer

1. **RC Testing**: Pre-release builds are validated by the automated RC pipeline —
   [`scripts/release/README.md`](https://github.com/gke-labs/kube-agents/tree/main/scripts/release) is the canonical reference for how `rc_YYMMDDHHMM_<short_sha>` builds are created, tested end-to-end, and tagged `*_validated` on success.
2. **SemVer Publication**: Running the release workflow (`release-publish.yml`) for a validated commit or tag promotes and publishes immutable artifacts (example for `1.2.0`):
   - **GHCR Images**: Clean promotion retags verified commit images to `ghcr.io/gke-labs/kube-agents/platform-agent:1.2.0` (and all required images) without rebuilding.
   - **OCI Helm Charts**: `oci://ghcr.io/gke-labs/kube-agents/charts/kube-agents:1.2.0` (packaged and signed by digest via `release-publish.yml`).
   - **Terraform Modules**: Sourced via Git tag reference `?ref=1.2.0`

## Helm Chart Versioning

The chart `version` tracks the application `appVersion`: the release workflow packages the
chart with both `version` and `appVersion` set to the exact SemVer release tag `X.Y.Z`, so every
chart release corresponds to exactly one application release. There is no chart-only release
train — a chart-template fix ships with the next `X.Y.Z` tag.

## Pinning Terraform Module Versions in GitOps

When forking the GitOps reference repository (`examples/gitops-repo/`), source Terraform modules using explicit SemVer Git tags:

```hcl
module "gke_cluster" {
  source       = "git::https://github.com/gke-labs/kube-agents.git//terraform/modules/gke-cluster?ref=1.2.0"
  project_id   = var.project_id
  cluster_name = "production-host-01"
  location     = "us-central1"
}
```
