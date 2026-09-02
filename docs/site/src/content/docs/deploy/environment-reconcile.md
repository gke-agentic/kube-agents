---
title: Reconciling the long-lived environments
description: How autopush and staging are kept in step with terraform/examples/full-install, what each one has to be configured with, and what to do when a drift report opens.
sidebar:
  order: 8
---

`autopush` and `staging` are long-lived: they are installed once and then kept
running, and people live-test pull requests against them. `rc` and `nightly` are
the opposite — every pipeline run destroys them and builds them again from
`terraform/examples/full-install`, so they always run today's composition.

That difference used to mean the two long-lived environments never ran Terraform
at all. The redeploy workflows are `helm upgrade` on a pre-existing release and
nothing more, so every infrastructure change that landed on `main` — IAM
bindings, Pub/Sub topics, node pools, cluster settings, and the chart values the
composition renders — was invisible in both until somebody re-applied by hand.
Both were found a month behind while a green redeploy reported that `main` was
deployed.

Three things now keep them in step.

## The scheduled drift report

`Drift: Long-Lived Environments` runs `terraform plan` against each environment
every morning and opens an issue labelled `infra-drift` when the plan is not
empty. It is read-only: no state lock, no state bucket creation, no adoption
imports, so it is safe against an environment somebody is working on and needs
no lease.

One issue per environment, edited in place while the drift lasts and closed
automatically by the first clean plan. A plan that fails to run leaves whatever
is open exactly as it is — a failure is not evidence either way, and the red job
is the signal.

The plan pins no image tag. It reads the tag the install is already serving, so
the report is about infrastructure rather than about images being a few commits
behind between redeploys.

## The nightly reconcile

The nightly pipeline applies the composition to both environments once its E2E
matrix is green — steps 4 and 6 of `nightly-pipeline.yml`. The ordering is the
point: a composition that has not been proved to build an install from nothing
does not get applied to an environment people work in.

The two are reconciled differently, and the difference is deliberate:

- **staging** is reconciled to the candidate the pipeline is promoting, and
  **before** the `staging_*` tag is pushed. That tag starts three
  `helm upgrade`s on the same release `helm_release.kube_agents` owns, so
  applying afterwards would race them.
- **autopush** is reconciled with no image tag at all. It tracks `main`'s tip
  through GHCR publishes, and the pipeline's candidate is older than that;
  pinning it would roll autopush's images backwards.

A reconcile takes the live-test lease before it applies anything (see
[`docs/designs/live-test-lease.md`](https://github.com/gke-labs/kube-agents/blob/main/docs/designs/live-test-lease.md)),
and defers to the next night if somebody else holds it. It
also waits out any redeploy already in flight, for the same release-lock reason
as the ordering above.

Run one by hand with `Shared: Reconcile Environment` (`mode: apply`), or locally
against your own install with `./upgrade.sh --plan` to see what a reconcile
would change.

## The rebuild button

When an in-place apply cannot converge, `Shared: Deploy Environment` now accepts
`autopush` and `staging` as well as `rc` and `nightly`. It **destroys the
cluster** and builds it again, so it asks you to type the environment's name
into `confirm_destroy`, and it refuses outright while the live-test lease is
held.

Read [what a teardown does not preserve](#what-a-rebuild-does-not-preserve)
before using it.

## What each environment has to be configured with

The reconcile renders an `install.env` from the environment's GitHub variables
and secrets — `scripts/release/render_install_env.sh` is the only mapping
between the two, and `install.env.example` documents what each key means.

Every setting below is **required** on a long-lived environment, and the
reconcile fails naming all the missing ones at once rather than starting. This
is not pedantry: an omitted setting resolves to a project default, the default
is written into `terraform.tfvars`, and `terraform apply` then plans the
destruction of whatever the default does not mention. On an environment that is
rebuilt every run that costs a feature; on one that has been up for a month it
takes the gVisor node pool, the Hindsight database, or the Pub/Sub topic behind
Google Chat with it.

| GitHub variable                 | install.env key                 | Notes                                                       |
| ------------------------------- | ------------------------------- | ----------------------------------------------------------- |
| `GCP_PROJECT_ID`                | `PROJECT_ID`                    | Required everywhere, including for a plan                   |
| `GCP_REGION`                    | `REGION`                        | Required everywhere                                         |
| `GKE_CLUSTER_NAME`              | `CLUSTER_NAME`                  | Required everywhere                                         |
| `GOOGLE_CHAT_ENABLED`           | `GOOGLE_CHAT_ENABLED`           | `false` removes the topic and subscription                  |
| `MODEL_PROVIDER`                | `MODEL_PROVIDER`                | Absent falls back to `gemini`                               |
| `PLATFORM_AGENT_PERMISSION_SET` | `PLATFORM_AGENT_PERMISSION_SET` | Absent falls back to `read-only` and drops the custom roles |
| `ENABLE_GVISOR`                 | `ENABLE_GVISOR`                 | Absent destroys the gVisor node pool on Standard            |
| `MEMORY_PROVIDER`               | `MEMORY`                        | Absent destroys the Hindsight API and its Postgres          |
| `USER_PROFILE_ENABLED`          | `USER_PROFILE_ENABLED`          | Absent resets it                                            |
| `ENABLE_GKE_BACKUP_PLAN`        | `ENABLE_GKE_BACKUP_PLAN`        | Absent destroys the backup plan                             |

Optional, and copied through when set: `CLUSTER_MODE`, `MODEL_DEFAULT_NAME`,
`VERTEX_PROJECT_ID`, `VERTEX_LOCATION`, `GOOGLE_CHAT_MODE`, `CHAT_TOPIC_NAME`,
`CHAT_SUB_NAME`, `ALLOWED_USERS`, `SLACK_ENABLED`, `SLACK_ALLOWED_USERS`,
`SLACK_HOME_CHANNEL`, `SLACK_HOME_CHANNEL_NAME`, `PLATFORM_AGENT_CUSTOM_ROLES`,
`HERMES_DASHBOARD_ENABLED`, `REGISTRY_PREFIX`, `THIRD_PARTY_REGISTRY_PREFIX`,
`KMS_KEYRING`, `KMS_KEY`, `GITOPS_ORG`, `GITOPS_REPO`. Secrets: `GH_APP_ID`,
`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `SLACK_BOT_TOKEN`,
`SLACK_APP_TOKEN`.

Two naming details that are easy to trip over:

- The namespace is `AGENT_NAMESPACE` on `rc` and `nightly` and `NAMESPACE` on
  `staging`. Both are read, so neither has to be renamed while installs are
  running against it.
- `GITOPS_ORG`/`GITOPS_REPO` name the repository the **agent** opens pull
  requests against. `GH_ORG`/`GH_REPO` name the **release** repository. Setting
  the minter's pair to the release repository scopes a live GitHub App token at
  this repository, which is why `rc` points at a throwaway repo instead.

`GITOPS_ORG`, `GITOPS_REPO` and `GH_APP_ID` are checked together: all three set
provisions the token minter, none set installs without it, and any other
combination is refused before anything is torn down. An environment carrying
`GH_APP_ID` alone — which is how `autopush` was configured — has to either gain
the other two or drop the secret.

## What a rebuild does not preserve

Only relevant to `Shared: Deploy Environment`; the nightly reconcile keeps the
cluster and everything on it.

- **KMS key rings survive.** GCP cannot delete them, and `lifecycle.sh adopt-kms`
  re-adopts them on the next apply. Already handled.
- **Pub/Sub subscription IAM is recreated**, not preserved, and the Google Chat
  app in the Workspace console points at the topic by name. Verify chat delivery
  after a rebuild.
- **The cluster endpoint changes.** Anything holding a kubeconfig — another
  agent's `live-test-envs.json`, a developer's machine — needs new credentials.
- **Anything an agent created on the cluster is not cleaned up.** Clusters
  provisioned _from inside_ autopush keep their own Terraform state in a bucket
  this composition does not manage, and a teardown of the host leaves them
  running.

## Applying repeatedly against an environment that exists

Two fixes landed with the reconcile because a scheduled apply is the first thing
in this project to run `terraform apply` against the same environment over and
over — `rc` and `nightly` dodge both by destroying first.

`lifecycle.sh apply` now adopts a pre-existing Pub/Sub topic and subscription
instead of failing with `Error 409: Resource already exists`, the same way it
already adopted KMS key rings. Configuring the Google Chat app in the Cloud
console creates the topic before the installer ever runs, so this was reachable
on a first install too.

And every Pub/Sub IAM binding is now keyed on its parent's `.id` rather than its
`.name`. GCP purges a topic's or subscription's IAM policy when the resource is
replaced; a binding keyed on the plan-time-known `.name` was excluded from the
very plan that replaced its parent, so the apply went green over an empty policy
and the credential proxy started answering chat pulls with HTTP 503.
