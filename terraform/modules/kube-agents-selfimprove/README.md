# Self-Improvement Loop Identity Module

The Google half of the self-improvement loop: the investigator's service account and its read-only
telemetry grants. That is the whole module. The Kubernetes half is the chart's `selfImprovement.*`
values.

The loop's GitHub identity has no GCP resource behind it. In `fork` and `upstream` mode the loop
authenticates as a robot account holding a personal access token, mounted from a Kubernetes Secret
that is created out of band, so there is nothing here to provision for it — no minter account, no
KMS signing key, no App. Sec. 6 of [`docs/designs/self-improvement.md`](../../../docs/designs/self-improvement.md)
records what that trades away against the GitHub App this module used to create.

The investigator's grant is three viewer roles and stops there. It has no `container.*` role at
all: Kubernetes reads go through the pod's Kubernetes service account, which the chart binds to
`view` in a single namespace, so that half is bounded by RBAC rather than by project-level IAM.
Adding `roles/container.viewer` here would quietly widen it to every cluster in the project.

The account also holds no GitHub credential, and that separation is one-directional and worth
stating precisely: a compromise of this service account cannot open a pull request, because the
token that does is a file in the credential-proxy sidecar and not anything IAM issues.

It does **not** mean a compromised investigation cannot open a pull request. The token sits in the
same pod. What stands between the two is inside that pod, not in IAM: the investigate turn is
started with the proxy shims off its `PATH` and the endpoint out of its environment, the Secret is
mounted into the sidecar and not the runner, and the proxy's deny policy refuses the argv shapes
that would abuse a token a turn did reach. None of the three is a boundary — the proxy is a sidecar
on unauthenticated loopback in the same pod. `agents/selfimprove/SOUL.md` says so to the agent
directly, and the design's §11 records splitting the filing turn into a second pod as the
structural fix, and as work this does not do.

This module is deliberately separate from `kube-agents-iam`, which grants the Platform Agent
whatever fleet management needs — read-only by default, and `roles/container.admin` under the
full-install composition's `permission_set = "gke-admin"`. The loop must inherit none of it —
an agent that can modify the cluster it is investigating cannot honestly report on it — and a
separate module also means an install can destroy the loop's identity without touching the
product's.

## The token, which is not created here

`selfImprovement.mode: report-only` needs no GitHub credential at all; its entire output is a ledger
ConfigMap in the release namespace. `fork` and `upstream` need one Secret, created by hand once:

```bash
kubectl -n kubeagents-system create secret generic kube-agents-selfimprove-pat \
  --from-literal=token=<the robot account's personal access token>
```

Name it in `selfImprovement.github.patSecret`. A classic token needs the `repo` scope — or
`public_repo` if every repository involved is public — held by an account with write access to
`selfImprovement.github.forkRepo`. One token covers both repositories under `upstream` mode, which
is what a GitHub App could not do: fork and base are different installations and `gh` stores one
token per host.

Use a robot account, not a person's. Nothing in the install rotates or expires the token, so its
lifetime is an operator's to manage, and its blast radius is every repository the account can write
to rather than a per-repository rule set.

## Names have to match the chart

Two values are agreed between this module and the chart, and nothing checks them at apply time:

| Terraform            | Chart                            |
| -------------------- | -------------------------------- |
| `service_account_id` | `selfImprovement.github.gsaName` |
| `ksa_name`           | `selfImprovement.github.ksaName` |

Both are checked for shape in two places — the variable validation here and the chart's render-time
guard — because either can be applied without the other, and an id GCP rejects fails at apply while
an id the chart rejects fails at render. The cap is 30, which is GCP's own.

## Usage

```hcl
module "selfimprove" {
  source = "../../modules/kube-agents-selfimprove"

  project_id = var.project_id
  namespace  = "kubeagents-system"
}
```
