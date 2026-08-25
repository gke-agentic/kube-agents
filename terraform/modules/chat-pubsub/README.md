# Google Chat Pub/Sub Routing Module

Reusable Terraform module for provisioning the Google Chat → Pub/Sub inbound routing the Platform Agent's chat integration relies on: the chat-events topic and pull subscription, the Workspace Add-ons **and** Chat API service identities, and the IAM bindings that let Google Chat publish and the agent subscribe.

The Chat API service identity registration is load-bearing: without it, Google Chat silently delivers zero events (no publishes, no errors) and the "Service account email" field on the Chat API configuration page never populates, even though both registrations resolve to the same P4SA.

## Relationship to the install

This is the module the full-install composition (and therefore `install.sh` with
`--enable-google-chat`) uses for the Chat backend. The canonical identifiers (topic
`platform-agent-chat-events`, subscription `platform-agent-chat-events-sub`) are the
composition's `chat_topic_name`/`chat_subscription_name` defaults,
and the module's defaults mirror them.

The module outputs are the values the PlatformAgent CR's `googleChat` integration needs.

Those default names are project-wide constants, and the topic and subscription
survive any teardown that did not run through the state managing them — a lost
state file, a destroy that failed part-way, an earlier hand-rolled install.
Creating them again then fails with a 409 on both names. The composition ships
`lifecycle.sh adopt-pubsub` to import them instead; it is deliberate rather than
part of `apply`, because a same-named topic can belong to a second install rather
than be a leftover. See
[Teardown and re-apply](../../examples/full-install/README.md#teardown-and-re-apply).
Distinct names are what keep that adoption unambiguous; they are not on their own
enough to run two full installs in one project, which the composition does not
support today.

## Usage

```hcl
module "chat_pubsub" {
  source                      = "git::https://github.com/gke-labs/kube-agents.git//terraform/modules/chat-pubsub?ref=1.2.0"
  project_id                  = "my-gcp-project"
  agent_service_account_email = "kubeagents-platform-gsa@my-gcp-project.iam.gserviceaccount.com"
}
```

See the [Release versioning & promotion guide](../../../docs/site/src/content/docs/deploy/release-versioning.md) for SemVer pinning instructions.
