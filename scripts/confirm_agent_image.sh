#!/usr/bin/env bash
#
# Confirm the tag a deploy just set actually reached the gateway's pod template.
#
# The agent redeploy's only change is `helm upgrade --set
# platformAgent.deployment.image.tag=<sha>` on the PlatformAgent CR, and the
# operator is free to ignore it: resolveAgentImage
# (k8s-operator/internal/controller/manifest_helpers.go) reads
# spec.deployment.tag only when spec.deployment.image carries no tag or digest
# of its own. A `kubectl patch` pinning a full reference therefore decides the
# image, and the deploy's tag stops meaning anything.
#
# Such a pin survives, which is the part worth spelling out because the chart
# does render spec.deployment.image on every upgrade. It renders the bare
# repository, byte-identical release to release, and Helm's patch for a custom
# resource is computed between the previous and new rendered manifests -- a
# field the same in both is simply absent from it, so the live value is never
# touched. Observed on autopush: `helm get manifest` showed the untagged
# repository while the live CR carried a pinned reference, and managedFields
# recorded `kubectl-patch` rather than `helm` as the owner of that field across
# every deploy that followed.
#
# Nothing downstream caught it. With the resolved image unchanged the operator
# writes no new pod template, so `kubectl rollout status` returns success
# against a ReplicaSet that was already complete -- 31 seconds from upgrade to
# green. Autopush served an agent image nine days old while every deploy in
# that window reported success, and it surfaced only when someone asked why a
# merged fix was missing from the running agent.
#
# Every release image in the template is checked, not just the agent
# container's. The platform-agent container, the sandbox-credential-cleanup
# init container, and the envoy-credential-proxy sidecar all move with the
# deploy, the last of them a different repository on the same tag. They
# normally move together, because the sidecar's reference is derived from
# resolveAgentImage's output. They do not when the agent image is
# digest-pinned: that path falls back to spec.deployment.tag, so the agent
# freezes at its digest while the proxy beside it rolls forward on every
# deploy. The operator logs that and carries on.
#
# Which images count comes from images.json -- the first-party entries on the
# release tag policy -- matched on the trailing path segment of the
# repository. Matching on a registry prefix instead would be wrong for a
# mirrored install: `mirror_images.sh` writes <prefix>/<name> and
# thirdPartyImageRegistry falls back to imageRegistry, so a single-prefix
# mirror puts fluent-bit under the same prefix as the release images and a
# prefix rule would demand the deploy's tag of a third-party pin.
#
# Usage: confirm_agent_image.sh <namespace> <deployment> <tag>
#
# The budget is the operator's reconcile latency, not a rollout: it is
# event-driven off the CR write and normally lands in seconds. It deliberately
# sits outside the startupProbe < rollout gate < progressDeadlineSeconds
# ordering that tests/test_gateway_rollout_budgets.py pins, because it waits
# for the Deployment to be written at all -- before the clock those three share
# starts.

set -euo pipefail

namespace="${1:?usage: confirm_agent_image.sh <namespace> <deployment> <tag>}"
deployment="${2:?usage: confirm_agent_image.sh <namespace> <deployment> <tag>}"
tag="${3:?usage: confirm_agent_image.sh <namespace> <deployment> <tag>}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGES_JSON="${IMAGES_JSON:-${REPO_ROOT}/images.json}"

timeout="${AGENT_IMAGE_CONFIRM_TIMEOUT:-120}"
interval="${AGENT_IMAGE_CONFIRM_INTERVAL:-5}"

command -v jq >/dev/null 2>&1 || {
  echo "::error::jq is required to read ${IMAGES_JSON}."
  exit 1
}

# The images a release tags with its own version. Anything else in the pod
# template -- fluent-bit, and whatever a future template adds -- is pinned by
# its own upstream version and must never be expected to carry this tag.
release_names="$(jq -r '.images[] | select(.origin == "first-party" and .tagPolicy == "release") | .name' "$IMAGES_JSON")"
[ -n "$release_names" ] || {
  echo "::error::No first-party release images found in ${IMAGES_JSON}."
  exit 1
}

# name=image, one per line, init containers first.
readonly JSONPATH='{range .spec.template.spec.initContainers[*]}{.name}={.image}{"\n"}{end}{range .spec.template.spec.containers[*]}{.name}={.image}{"\n"}{end}'

stderr_file="$(mktemp)"
trap 'rm -f "$stderr_file"' EXIT

# Is this image one the release tags itself? Compares the repository's trailing
# path segment, which both the chart's repository rewrite and mirror_images.sh
# preserve.
#
# Take the segment before stripping the tag, not after. A registry with a port
# -- registry.local:5000/kube-agents/platform-agent -- puts a colon in the
# first path segment, so stripping the last `:...` off the whole reference
# eats the repository path and leaves the registry host. A digest-pinned image
# on such a registry then stops looking like a release image at all, and the
# failure below reports finding none rather than the pin it exists to name.
is_release_image() {
  local segment="${1%@*}"
  segment="${segment##*/}"
  segment="${segment%%:*}"
  grep -qxF "$segment" <<<"$release_names"
}

# Sets matched and mismatched from a name=image listing.
inspect_template() {
  matched=0
  mismatched=""

  local name image
  while IFS='=' read -r name image; do
    [ -n "$image" ] || continue
    is_release_image "$image" || continue
    matched=$((matched + 1))
    case "$image" in
      *:"$tag") ;;
      *) mismatched="${mismatched}  ${name}: ${image}"$'\n' ;;
    esac
  done <<<"$1"
}

deadline=$((SECONDS + timeout))
while true; do
  # A stale or missing read is the expected first answer, not a failure: the
  # operator reconciles asynchronously and the deploy returns before it does.
  # kubectl's own error is kept rather than discarded, because an expired
  # credential and a slow operator look identical from here until the deadline.
  listing="$(kubectl get "deployment/${deployment}" -n "$namespace" -o jsonpath="$JSONPATH" 2>"$stderr_file" || true)"
  inspect_template "$listing"

  if [ "$matched" -gt 0 ] && [ -z "$mismatched" ]; then
    echo "Operator applied tag ${tag} to all ${matched} release image(s) in ${deployment}."
    exit 0
  fi

  if [ "$SECONDS" -ge "$deadline" ]; then
    if [ "$matched" -eq 0 ]; then
      echo "::error::Found no first-party release image in ${deployment} after ${timeout}s. Read back:"
      echo "${listing:-  <nothing>}"
    else
      echo "::error::${deployment} is not running the tag ${tag} this deploy set. Still on:"
      printf '%s' "$mismatched"
    fi
    if [ -s "$stderr_file" ]; then
      echo "kubectl also reported:"
      sed 's/^/  /' "$stderr_file"
    fi
    # Two causes worth separating, and the CR tells them apart: a tag or digest
    # baked into spec.deployment.image makes the operator ignore
    # spec.deployment.tag, while an operator that is absent, crash-looping, or
    # returning early never re-renders the pod template at all. Which one it is
    # decides the remedy printed below, because a pin is not the only way to
    # get here and naming it unconditionally sends the reader after a CR that
    # is fine. Only the image and tag are read: DeploymentSpec also carries env
    # with literal values, and these logs are public.
    cr_listing="$(kubectl get platformagent -n "$namespace" \
      -o jsonpath='{range .items[*]}{.metadata.name}={.spec.deployment.image}={.spec.deployment.tag}{"\n"}{end}' 2>/dev/null || true)"
    pinned=""
    if [ -n "$cr_listing" ]; then
      echo "spec.deployment on the CR:"
      while IFS='=' read -r cr_name cr_image cr_tag; do
        [ -n "$cr_name" ] || continue
        echo "  ${cr_name}: image=${cr_image} tag=${cr_tag}"
        case "${cr_image##*/}" in
          *:* | *@*) pinned="yes" ;;
        esac
      done <<<"$cr_listing"
    fi
    echo "operator status:"
    kubectl get platformagent -n "$namespace" \
      -o jsonpath='{range .items[*]}  {.metadata.name}: {.status.phase}{"\n"}{range .status.conditions[*]}    {.type}={.status} {.reason}{"\n"}{end}{end}' || true
    if [ -n "$pinned" ]; then
      echo "An image above carries a tag or digest. That pin outranks spec.deployment.tag, the only field this deploy sets. Clear it with:"
      echo "  kubectl patch platformagent <name> -n ${namespace} --type=merge \\"
      echo "    -p '{\"spec\":{\"deployment\":{\"image\":\"<repository, no tag>\"}}}'"
    else
      echo "No CR above pins spec.deployment.image, so a pin is not the cause here. Read the status: an operator that is absent, crash-looping, or returning early leaves the pod template as it was."
    fi
    exit 1
  fi

  sleep "$interval"
done
