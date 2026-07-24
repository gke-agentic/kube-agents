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
    echo "[INFO] Downloading crane CLI tool..." >&2
    CRANE_VER=$(curl -sSL https://api.github.com/repos/google/go-containerregistry/releases/latest 2>/dev/null | grep '"tag_name":' | sed -E 's/.*"([^"]+)".*/\1/' || echo "")
    CRANE_VER="${CRANE_VER:-v0.20.2}"
    curl -sSL "https://github.com/google/go-containerregistry/releases/download/${CRANE_VER}/go-containerregistry_Linux_x86_64.tar.gz" | tar -xz -C /tmp crane
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
