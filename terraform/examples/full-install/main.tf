locals {
  base_apis = [
    "container.googleapis.com",
    "cloudkms.googleapis.com",
    "iam.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
    # Unconditional, like provision_01_gcp_cluster.sh: the cluster is created
    # with the Backup for GKE agent enabled whether or not a BackupPlan
    # follows, and the addon cannot be enabled without the API.
    "gkebackup.googleapis.com",
  ]
  chat_apis = var.enable_google_chat ? [
    "pubsub.googleapis.com",
    "chat.googleapis.com",
    "gsuiteaddons.googleapis.com",
  ] : []

  use_vertex      = var.model_provider == "vertex_ai"
  vertex_project  = var.vertex_project_id != "" ? var.vertex_project_id : var.project_id
  vertex_location = var.vertex_location != "" ? var.vertex_location : var.location
  litellm_ksa     = "kubeagents-litellm"

  required_apis = toset(concat(local.base_apis, local.chat_apis))

  # The permission sets provision_04_gcp_iam.sh grants, kept verbatim so the
  # two install paths hand the agent the same authority. Kubernetes RBAC is
  # read-only in both; see the security-and-iam reference.
  read_only_roles = [
    "roles/container.clusterViewer",
    "roles/container.viewer",
    "roles/compute.viewer",
    "roles/monitoring.viewer",
    "roles/logging.viewer",
    "roles/iam.serviceAccountUser",
    "roles/iam.securityReviewer",
    "roles/mcp.toolUser",
  ]
  gke_admin_roles = [
    "roles/container.clusterAdmin",
    "roles/container.admin",
    "roles/compute.viewer",
    "roles/monitoring.admin",
    # The agent can query logs for diagnostics but must not administer the
    # audit-log sink.
    "roles/logging.viewer",
    "roles/iam.serviceAccountUser",
    "roles/iam.securityReviewer",
    "roles/mcp.toolUser",
  ]

  # An explicit project_roles list always wins, so an existing configuration
  # that set it keeps its roles regardless of permission_set.
  agent_project_roles = (
    var.project_roles != null
    ? var.project_roles
    : (var.permission_set == "gke-admin" ? local.gke_admin_roles : local.read_only_roles)
  )

  # Only non-empty credential keys end up in the Secret, so an unset optional
  # provider key does not create an empty entry.
  optional_credentials = {
    for key, value in {
      API_SERVER_KEY = var.api_server_key
      # Generated rather than asked for: neither value means anything to an
      # operator, and both are scoped to the agent pod. Held in Terraform state
      # rather than left to the chart's own generation so that `terraform apply`
      # is idempotent without needing a cluster read — rotating the salt would
      # re-anonymise every user, breaking the link between their past sessions
      # and their future ones.
      SESSION_KV_API_KEY = random_password.session_kv_api_key.result
      SESSION_KV_SALT    = random_password.session_kv_salt.result
      ANTHROPIC_API_KEY  = var.anthropic_api_key
      GEMINI_API_KEY     = var.gemini_api_key
      OPENAI_API_KEY     = var.openai_api_key
    } : key => value if value != ""
  }

  # Slack is the exception to that filter, and has to be: with the integration
  # enabled the CR names both keys in a secretKeyRef the operator passes
  # through verbatim (no `optional: true` — see defaultSecretRef in
  # manifest_helpers.go), so a key missing from the Secret does not disable
  # Slack, it holds the whole agent pod in CreateContainerConfigError. The
  # tokens legitimately arrive after the first apply, because creating the
  # Slack app is a manual step, so an empty value has to reach the Secret as
  # an empty value. provision_07_gcp_k8s_secrets.sh writes the pair
  # unconditionally for the same reason.
  slack_credentials = var.enable_slack ? {
    SLACK_BOT_TOKEN = var.slack_bot_token
    SLACK_APP_TOKEN = var.slack_app_token
  } : {}

  credentials = merge(local.optional_credentials, local.slack_credentials)
}

# A warning rather than a precondition: an install that enables Slack before
# the Slack app exists is a legitimate order of operations, and the empty keys
# above keep the pod running until the tokens land. What is not legitimate is
# not being told.
check "slack_tokens_present" {
  assert {
    condition     = !var.enable_slack || (var.slack_bot_token != "" && var.slack_app_token != "")
    error_message = "enable_slack is true but slack_bot_token and/or slack_app_token is empty. The agent pod will start and Slack will stay silent until both tokens are set in the credentials Secret."
  }
}

# Bearer token for the pod-local Session KV server on 127.0.0.1:8699. Both the
# sandbox container (which serves and calls it) and the credential-proxy
# container (whose event watcher posts to it) read this one value.
resource "random_password" "session_kv_api_key" {
  length  = 48
  special = false
}

# HMAC salt for pseudonymising chat identities before they reach session
# metadata, audit logs, or OTel spans.
resource "random_password" "session_kv_salt" {
  length  = 48
  special = false
}

resource "google_project_service" "required" {
  for_each = local.required_apis

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

module "gke_cluster" {
  source = "../../modules/gke-cluster"

  project_id                 = var.project_id
  cluster_name               = var.cluster_name
  location                   = var.location
  deletion_protection        = var.deletion_protection
  release_channel            = var.release_channel
  enable_database_encryption = var.enable_database_encryption
  kms_keyring_name           = var.kms_keyring_name
  kms_key_name               = var.kms_key_name
  enable_backup_agent        = var.enable_backup_agent
  resource_labels = {
    "kube-agents-host" = "true"
  }

  depends_on = [google_project_service.required]
}

module "gke_backup_plan" {
  source = "../../modules/gke-backup-plan"
  count  = var.enable_gke_backup_plan ? 1 : 0

  project_id          = var.project_id
  cluster_name        = module.gke_cluster.cluster_name
  location            = module.gke_cluster.cluster_location
  selected_namespaces = [var.namespace]
  cron_schedule       = var.backup_cron_schedule
  backup_retain_days  = var.backup_retain_days
  encryption_key      = var.backup_encryption_key
}

module "kube_agents_iam" {
  source = "../../modules/kube-agents-iam"

  project_id    = var.project_id
  namespace     = var.namespace
  project_roles = local.agent_project_roles

  depends_on = [google_project_service.required]
}

# ─── Vertex AI gateway identity (model_provider = "vertex_ai") ────────────────
# Vertex has no API key: the LiteLLM gateway calls it as this GSA through
# Workload Identity. The GSA lives in project_id; the aiplatform.user grant and
# the API enablement go to the serving project, which may be a different one.
resource "google_project_service" "vertex_ai" {
  count = local.use_vertex ? 1 : 0

  project            = local.vertex_project
  service            = "aiplatform.googleapis.com"
  disable_on_destroy = false
}

module "litellm_vertex_iam" {
  source = "../../modules/kube-agents-iam"
  count  = local.use_vertex ? 1 : 0

  project_id         = var.project_id
  service_account_id = "kubeagents-litellm-gsa"
  display_name       = "Kube-Agents LiteLLM Vertex AI Service Account"
  namespace          = var.namespace
  ksa_name           = local.litellm_ksa
  # Granted below instead, so a cross-project vertex_project_id works.
  project_roles = []

  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "litellm_vertex_user" {
  count = local.use_vertex ? 1 : 0

  project = local.vertex_project
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${module.litellm_vertex_iam[0].service_account_email}"

  depends_on = [google_project_service.vertex_ai]
}

module "chat_pubsub" {
  source = "../../modules/chat-pubsub"
  count  = var.enable_google_chat ? 1 : 0

  project_id                  = var.project_id
  agent_service_account_email = module.kube_agents_iam.service_account_email

  depends_on = [google_project_service.required]
}

module "github_minter" {
  source = "../../modules/github-minter"
  count  = var.enable_github_minter ? 1 : 0

  project_id = var.project_id
  location   = var.location
  namespace  = var.namespace

  depends_on = [google_project_service.required]
}

resource "helm_release" "kube_agents" {
  name             = "kube-agents"
  chart            = "${path.module}/../../../charts/kube-agents"
  namespace        = var.namespace
  create_namespace = true

  values = [yamlencode({
    operator = {
      image = {
        tag = var.image_tag
      }
    }
    litellm = merge(
      {
        modelProvider    = var.model_provider
        modelDefaultName = var.model_default_name
      },
      local.use_vertex ? {
        vertex = {
          serviceAccountName = local.litellm_ksa
          serviceAccountAnnotations = {
            "iam.gke.io/gcp-service-account" = module.litellm_vertex_iam[0].service_account_email
          }
          projectId = local.vertex_project
          location  = local.vertex_location
        }
      } : {}
    )
    platformAgent = {
      harness = {
        clusterName = module.gke_cluster.cluster_name
        location    = module.gke_cluster.cluster_location
        projectId   = var.project_id
      }
      deployment = {
        image = {
          tag = var.image_tag
        }
      }
      security = {
        # With annotations set, the OPERATOR creates and manages the KSA (see
        # the chart README's ServiceAccount-ownership section); this one wires
        # Workload Identity to the GSA the kube-agents-iam module created.
        serviceAccountAnnotations = {
          "iam.gke.io/gcp-service-account" = module.kube_agents_iam.service_account_email
        }
      }
      credentials = {
        create = true
        data   = local.credentials
      }
      integration = merge(
        var.enable_google_chat ? {
          googleChat = {
            enabled          = true
            topicName        = module.chat_pubsub[0].topic_name
            subscriptionName = module.chat_pubsub[0].subscription_name
            allowedUsers     = var.google_chat_allowed_users
            homeChannel      = var.google_chat_home_channel
            mode             = var.google_chat_mode
          }
        } : {},
        var.enable_slack ? {
          slack = {
            enabled         = true
            allowedUsers    = var.slack_allowed_users
            homeChannel     = var.slack_home_channel
            homeChannelName = var.slack_home_channel_name
          }
        } : {},
        var.github_repo != "" ? {
          github = { gitRepo = var.github_repo }
        } : {}
      )
    }
  })]

  # The Vertex entries are no-ops when model_provider is not "vertex_ai"; without
  # them the gateway can be serving before its API and role grant land.
  depends_on = [module.gke_cluster, google_project_service.vertex_ai, google_project_iam_member.litellm_vertex_user]
}
