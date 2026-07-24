#!/usr/bin/env bash
# ==============================================================================
# 🛠️ E2E Test Scripts Common Shared Utilities
# ==============================================================================

set -euo pipefail

REGISTRY="${REGISTRY:-ghcr.io}"
OWNER="${OWNER:-gke-agentic/kube-agents}"
IMAGES="${IMAGES:-platform-agent}"
SOURCE_TAG="${SOURCE_TAG:-latest}"

# Ensure crane CLI is installed and exported
ensure_crane() {
  if command -v crane &>/dev/null; then
    CRANE_BIN="crane"
  elif [ -f "/tmp/crane" ]; then
    CRANE_BIN="/tmp/crane"
  else
    echo "[INFO] Downloading crane CLI tool via gh CLI..." >&2
    if command -v gh &>/dev/null; then
      gh release download -R google/go-containerregistry --pattern "*_Linux_x86_64.tar.gz" --dir /tmp --clobber >/dev/null 2>&1 || true
      tar -xzf /tmp/*_Linux_x86_64.tar.gz -C /tmp crane 2>/dev/null || true
    fi

    if [ ! -f "/tmp/crane" ]; then
      curl -sSL "https://github.com/google/go-containerregistry/releases/download/v0.21.7/go-containerregistry_Linux_x86_64.tar.gz" | tar -xz -C /tmp crane
    fi

    chmod +x /tmp/crane
    CRANE_BIN="/tmp/crane"
  fi
  export CRANE_BIN

  if [ -n "${GITHUB_TOKEN:-}" ]; then
    "${CRANE_BIN}" auth login "${REGISTRY:-ghcr.io}" -u "${GITHUB_ACTOR:-${USER}}" -p "${GITHUB_TOKEN}" >/dev/null 2>&1 || true
  fi
}

# Convert image name to variable prefix (e.g. platform-agent -> platform_agent)
to_var_prefix() {
  local img="$1"
  echo "$img" | tr '-' '_'
}
