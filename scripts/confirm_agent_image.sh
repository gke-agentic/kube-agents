#!/usr/bin/env bash
#
# Confirm the tag a deploy just set actually reached the gateway's pod template.
#
# The agent redeploy's only change is `helm upgrade --set
# platformAgent.deployment.image.tag=<sha>` on the PlatformAgent CR, and the
# operator is free to ignore it: resolveAgentImage
# (k8s-operator/internal/controller/manifest_helpers.go) reads
# spec.deployment.tag only when spec.deployment.image carries no tag or digest
# of its own. A `kubectl patch` pinning a full reference therefore outranks
# every later deploy for as long as it sits there.
#
# Nothing downstream caught that. With the resolved image unchanged the
# operator writes no new pod template, so `kubectl rollout status` returns
# success against a ReplicaSet that was already complete -- 31 seconds from
# upgrade to green. Autopush served a nine-day-old agent through nine
# consecutive green deploys that way, and it surfaced only when someone asked
# why a merged fix was missing from the running agent.
#
# Every release image in the template is checked, not just the agent
# container's. Three of the gateway's four images move with the deploy -- the
# platform-agent container, the sandbox-credential-cleanup init container, and
# the envoy-credential-proxy sidecar, which is a different repository on the
# same tag. They normally move together, because the sidecar's reference is
# derived from resolveAgentImage's output. They do not when the agent image is
# digest-pinned: that path falls back to spec.deployment.tag, so the agent
# freezes at its digest while the proxy beside it rolls forward on every
# deploy. The operator logs that and carries on.
#
# Which images count is read off the template rather than listed here. The
# repository prefix of whichever image ends in /platform-agent is the release's
# own registry path, and every image sharing it must carry the requested tag.
# That follows an install which mirrors the images to its own registry, and
# leaves genuinely third-party pins -- fluent-bit -- alone.
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

timeout="${AGENT_IMAGE_CONFIRM_TIMEOUT:-120}"
interval="${AGENT_IMAGE_CONFIRM_INTERVAL:-5}"

# name=image, one per line, init containers first.
readonly JSONPATH='{range .spec.template.spec.initContainers[*]}{.name}={.image}{"\n"}{end}{range .spec.template.spec.containers[*]}{.name}={.image}{"\n"}{end}'

# Sets release_prefix, matched, and mismatched from a name=image listing.
inspect_template() {
  release_prefix=""
  matched=0
  mismatched=""

  local name image
  while IFS='=' read -r name image; do
    [ -n "$image" ] || continue
    case "$image" in
      */platform-agent:* | */platform-agent@*) release_prefix="${image%/*}/" ;;
    esac
  done <<<"$1"

  [ -n "$release_prefix" ] || return 0

  while IFS='=' read -r name image; do
    [ -n "$image" ] || continue
    case "$image" in
      "$release_prefix"*) ;;
      *) continue ;;
    esac
    matched=$((matched + 1))
    case "$image" in
      *:"$tag") ;;
      *) mismatched="${mismatched}  ${name}: ${image}"$'\n' ;;
    esac
  done <<<"$1"
}

deadline=$((SECONDS + timeout))
while true; do
  # A missing Deployment is indistinguishable from a slow one this early, so an
  # empty read retries rather than failing; the deadline covers both.
  listing="$(kubectl get "deployment/${deployment}" -n "$namespace" -o jsonpath="$JSONPATH" 2>/dev/null || true)"
  inspect_template "$listing"

  if [ -n "$release_prefix" ] && [ "$matched" -gt 0 ] && [ -z "$mismatched" ]; then
    echo "Operator applied tag ${tag} to all ${matched} release image(s) in ${deployment}."
    exit 0
  fi

  if [ "$SECONDS" -ge "$deadline" ]; then
    if [ -z "$release_prefix" ]; then
      echo "::error::Could not find a platform-agent image in ${deployment} after ${timeout}s. Read back:"
      echo "${listing:-  <nothing>}"
      exit 1
    fi
    echo "::error::${deployment} is not running the tag ${tag} this deploy set. The deploy changed nothing:"
    printf '%s' "$mismatched"
    echo "spec.deployment on the CR decides this — an image with a tag or digest baked in makes the operator ignore spec.deployment.tag, the only field the deploy sets:"
    kubectl get platformagent platform-agent -n "$namespace" -o jsonpath='{.spec.deployment}' || true
    echo
    echo "Clear a pin with:"
    echo "  kubectl patch platformagent platform-agent -n ${namespace} --type=merge \\"
    echo "    -p '{\"spec\":{\"deployment\":{\"image\":\"<repository, no tag>\"}}}'"
    exit 1
  fi

  sleep "$interval"
done
