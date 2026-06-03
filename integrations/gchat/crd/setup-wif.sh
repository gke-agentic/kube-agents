#!/usr/bin/env bash
# ==============================================================================
# 🤖 Google Cloud Workload Identity Federation (WIF) Setup Script
# ==============================================================================
# Sets up a secure OpenID Connect (OIDC) identity provider for GitHub Actions.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VARS_FILE="${SCRIPT_DIR}/vars.sh"

# Colors
C_CYAN='\033[96m'
C_GREEN='\033[92m'
C_YELLOW='\033[93m'
C_RED='\033[91m'
C_RESET='\033[0m'
C_BOLD='\033[1m'

print_step() { echo -e "\n${C_BOLD}${C_CYAN}>>> $1${C_RESET}"; }
print_success() { echo -e "  ${C_GREEN}✓ $1${C_RESET}"; }
print_info() { echo -e "  ${C_CYAN}ℹ $1${C_RESET}"; }
print_error() { echo -e "  ${C_RED}✗ $1${C_RESET}"; exit 1; }

# Load variables if they exist
if [ -f "$VARS_FILE" ]; then
  print_info "Loading GCP configuration from ${VARS_FILE}"
  source "$VARS_FILE"
fi

# Fallback project ID resolution
if [ -z "$PROJECT_ID" ]; then
  PROJECT_ID="$(gcloud config get-value project 2>/dev/null || echo "")"
  if [ -z "$PROJECT_ID" ]; then
    print_error "GCP Project ID not set. Please set PROJECT_ID in vars.sh or configure gcloud."
  fi
fi

# Fallback project Number resolution
if [ -z "$PROJECT_NUMBER" ]; then
  print_info "Resolving GCP Project Number..."
  PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null || echo "")
  if [ -z "$PROJECT_NUMBER" ]; then
    print_error "Failed to resolve GCP Project Number. Please set PROJECT_NUMBER in vars.sh."
  fi
fi

# Try to resolve Git remote repository name automatically
if [ -z "$GITHUB_REPO" ]; then
  REMOTE_URL=$(git remote get-url origin 2>/dev/null || echo "")
  if [[ "$REMOTE_URL" =~ github.com[:/]([^/]+/[^.]+)(\.git)? ]]; then
    GITHUB_REPO="${BASH_REMATCH[1]}"
    print_info "Auto-detected GitHub Repository: ${GITHUB_REPO}"
  else
    echo -ne "  ${C_CYAN}Enter GitHub Repository (owner/repo): ${C_RESET}"
    read -r GITHUB_REPO
    if [ -z "$GITHUB_REPO" ]; then
      print_error "GitHub repository name is required."
    fi
  fi
fi

POOL_NAME="github-pool"
PROVIDER_NAME="github-provider"
SERVICE_ACCOUNT_NAME="github-actions-sa"
SA_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# 1. Create Workload Identity Pool
print_step "1. Creating Workload Identity Pool..."
if ! gcloud iam workload-identity-pools describe "${POOL_NAME}" --project="${PROJECT_ID}" --location="global" &>/dev/null; then
  gcloud iam workload-identity-pools create "${POOL_NAME}" \
      --project="${PROJECT_ID}" \
      --location="global" \
      --display-name="GitHub Actions Pool"
  print_success "Workload Identity Pool '${POOL_NAME}' created."
else
  print_success "Workload Identity Pool '${POOL_NAME}' already exists."
fi

# 2. Add GitHub as a Trusted OIDC Provider
print_step "2. Configuring OIDC Provider..."
if ! gcloud iam workload-identity-pools providers describe "${PROVIDER_NAME}" \
    --project="${PROJECT_ID}" \
    --location="global" \
    --workload-identity-pool="${POOL_NAME}" &>/dev/null; then
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_NAME}" \
      --project="${PROJECT_ID}" \
      --location="global" \
      --workload-identity-pool="${POOL_NAME}" \
      --display-name="GitHub Actions Provider" \
      --issuer-uri="https://token.actions.githubusercontent.com" \
      --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
      --attribute-condition="assertion.repository == '${GITHUB_REPO}'"
  print_success "OIDC Provider '${PROVIDER_NAME}' added to pool."
else
  print_success "OIDC Provider '${PROVIDER_NAME}' already configured."
fi

# 3. Create GSA if not exists
print_step "3. Creating Service Account..."
if ! gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" &>/dev/null; then
  gcloud iam service-accounts create "${SERVICE_ACCOUNT_NAME}" \
      --project="${PROJECT_ID}" \
      --display-name="GitHub Actions CI/CD SA"
  print_success "Service Account '${SA_EMAIL}' created."
else
  print_success "Service Account '${SA_EMAIL}' already exists."
fi

# 4. Grant Artifact Registry permissions to the Service Account
print_step "4. Granting Registry Publisher permissions..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/artifactregistry.writer" \
    --quiet &>/dev/null
print_success "roles/artifactregistry.writer granted to ${SA_EMAIL}."

# 5. Bind GitHub repository to the Service Account
print_step "5. Binding Workload Identity to GitHub Repository..."
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
    --project="${PROJECT_ID}" \
    --role="roles/iam.workloadIdentityUser" \
    --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_NAME}/attribute.repository/${GITHUB_REPO}" \
    --quiet &>/dev/null
print_success "WIF policy bound: Only pushes from ${GITHUB_REPO} can assume ${SERVICE_ACCOUNT_NAME}."

# Print GitHub configuration details
WIF_PROVIDER_URI="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_NAME}/providers/${PROVIDER_NAME}"

echo -e "\n${C_GREEN}${C_BOLD}==============================================================================${C_RESET}"
echo -e "${C_GREEN}${C_BOLD}🚀 GCP WORKLOAD IDENTITY FEDERATION CONFIGURED SUCCESSFULLY!${C_RESET}"
echo -e "${C_GREEN}${C_BOLD}==============================================================================${C_RESET}"
echo -e "\nConfigure the following variables in your **GitHub Repository Variables**:\n"
echo -e "  - ${C_BOLD}GCP_PROJECT_ID:${C_RESET}                   ${C_CYAN}${PROJECT_ID}${C_RESET}"
echo -e "  - ${C_BOLD}GCP_REGION:${C_RESET}                       ${C_CYAN}${REGION:-us-central1}${C_RESET}"
echo -e "  - ${C_BOLD}GAR_REPOSITORY:${C_RESET}                   ${C_CYAN}platform-agent-repo${C_RESET}"
echo -e "  - ${C_BOLD}GCP_WORKLOAD_IDENTITY_PROVIDER:${C_RESET}   ${C_CYAN}${WIF_PROVIDER_URI}${C_RESET}"
echo -e "  - ${C_BOLD}GCP_SERVICE_ACCOUNT:${C_RESET}              ${C_CYAN}${SA_EMAIL}${C_RESET}"
echo -e "\n${C_GREEN}${C_BOLD}==============================================================================${C_RESET}\n"
