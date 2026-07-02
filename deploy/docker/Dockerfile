# check=skip=InvalidDefaultArgInFrom
ARG HERMES_AGENT_TAG
FROM nousresearch/hermes-agent:${HERMES_AGENT_TAG} AS agent-base

USER root

# ==============================================================================
# OpenShell Gateway / Supervisor Compatibility
# ==============================================================================
# OpenShell requires that the container image contains both 'sandbox' (runs agent)
# and 'supervisor' (runs security sidecar) users.
#
# We use a dedicated high UID/GID (10001) for the sandbox user to avoid conflicts.
# ==============================================================================
RUN groupadd -r supervisor && \
    useradd -r -g supervisor -s /usr/sbin/nologin supervisor && \
    groupadd -g 10001 sandbox && \
    useradd -u 10001 -g sandbox -m -d /home/sandbox -s /bin/bash sandbox

# 1. Install all common system utilities and dependencies
# Includes OpenShell supervisor required networking and process tools
RUN apt-get update && apt-get install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    dnsutils \
    git \
    gnupg \
    iproute2 \
    iptables \
    nftables \
    iputils-ping \
    jq \
    nano \
    net-tools \
    netcat-openbsd \
    openssh-sftp-server \
    patch \
    procps \
    traceroute \
    vim \
    wget \
    && rm -rf /var/lib/apt/lists/*
 
# ==============================================================================
# Workaround: Mock 'ss' command to bypass netlink socket restriction
#
# On GKE Standard, the OpenShell supervisor runs the nested sandbox workload under
# a strict security policy (likely Seccomp) that blocks netlink socket operations.
# When kagent-controller runs its bootstrap check ('ss -tln' waiting for port 18642),
# it fails with: "Cannot open netlink socket: Operation not permitted".
#
# Since the gateway itself does not need netlink to listen on a port, we replace
# the real 'ss' binary with a mock script that intercepts the check for port 18642
# and always reports it as listening. Other ports/calls are forwarded to 'ss.real'.
# ==============================================================================
RUN mv /usr/bin/ss /usr/bin/ss.real && \
    printf '#!/bin/sh\n\
if echo "$@" | grep -q -E -- "-t.*-l|-l.*-t|-tln|-l"; then\n\
  if grep -q -E "[0-9A-F]{8}:48D2 [0-9A-F]{8}:[0-9A-F]{4} 0A" /proc/net/tcp 2>/dev/null; then\n\
    echo "LISTEN 0 128 127.0.0.1:18642 0.0.0.0:*"\n\
  fi\n\
else\n\
  /usr/bin/ss.real "$@" 2>/dev/null || true\n\
fi\n' > /usr/bin/ss && \
    chmod +x /usr/bin/ss

# 2. Install google-cloud-cli and kubectl natively using the Google Cloud apt repository
RUN curl -sSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | tee -a /etc/apt/sources.list.d/google-cloud-sdk.list && \
    apt-get update && apt-get install -y \
    google-cloud-cli \
    google-cloud-cli-gke-gcloud-auth-plugin \
    kubectl \
    && rm -rf /var/lib/apt/lists/*

# 3. Install GitHub CLI (gh), yq, k9s, and helm
RUN ARCH=$(dpkg --print-architecture) && \
    curl -fL -o /tmp/gh.deb "https://github.com/cli/cli/releases/download/v2.92.0/gh_2.92.0_linux_${ARCH}.deb" \
    && dpkg -i /tmp/gh.deb \
    && rm /tmp/gh.deb && \
    curl -sSLf -o /usr/local/bin/yq "https://github.com/mikefarah/yq/releases/download/v4.53.3/yq_linux_${ARCH}" && \
    chmod +x /usr/local/bin/yq && \
    curl -sSLf -o /tmp/k9s.tar.gz "https://github.com/derailed/k9s/releases/download/v0.51.0/k9s_Linux_${ARCH}.tar.gz" && \
    tar -xzf /tmp/k9s.tar.gz -C /usr/local/bin k9s && \
    rm /tmp/k9s.tar.gz && \
    curl -sSLf -o /tmp/helm.tar.gz "https://get.helm.sh/helm-v3.15.2-linux-${ARCH}.tar.gz" && \
    tar -xzf /tmp/helm.tar.gz -C /tmp && \
    mv /tmp/linux-${ARCH}/helm /usr/local/bin/helm && \
    rm -rf /tmp/helm.tar.gz /tmp/linux-${ARCH}

RUN /usr/local/bin/uv pip install --python /opt/hermes/.venv/bin/python \
    ddgs \
    google-api-python-client \
    google-auth \
    google-cloud-pubsub \
    "hermes-agent[google_chat]" \
    opentelemetry-api \
    opentelemetry-sdk \
    opentelemetry-exporter-otlp-proto-http
 
# ==============================================================================
# OpenShell Workaround: Hermes Wrapper Script for Secret Injection
#
# Because kagent-controller (when using OpenShell runtime) ignores environment
# variables defined using 'valueFrom' (secrets), we cannot inject API keys natively.
# Additionally, the OpenShell supervisor isolates the workload process and clears
# the container's default environment variables to prevent credential leakage.
#
# To bypass this securely:
# 1. We patch the Sandbox CR (via provision_harness.sh) to mount the secrets
#    secret ('platform-agent-secrets') as a read-only volume at /etc/secrets/platform-agent-secrets.
# 2. We replace the standard 'hermes' binary path with this wrapper script.
# 3. The wrapper dynamically loops through all mounted secret files, exports them
#    as environment variables in the workload shell context, and execs the real binary.
# ==============================================================================
RUN mv /opt/hermes/.venv/bin/hermes /opt/hermes/.venv/bin/hermes.real && \
    printf '#!/bin/sh\n\
# ==============================================================================\n\
# 🛡️ OpenShell Sandbox Environment Recovery & Proxy Workaround\n\
# ==============================================================================\n\
# 1. Restore Environment Variables from Mounted Secret:\n\
# OpenShell supervisor clears container environment variables. We restore them\n\
# by reading the mounted secret files.\n\
if [ -d "/etc/secrets/platform-agent-secrets" ]; then\n\
  for f in /etc/secrets/platform-agent-secrets/*; do\n\
    if [ -f "$f" ]; then\n\
      var_name=$(basename "$f")\n\
      export "$var_name"="$(cat "$f")"\n\
    fi\n\
  done\n\
fi\n\
#\n\
# 2. Configure Proxy Bypasses (no_proxy):\n\
# OpenShell propagates the egress proxy (10.200.0.1) but wipes the no_proxy list.\n\
# We bypass the proxy for GKE local services (.svc.cluster.local), Google APIs\n\
# (.googleapis.com), and internal RFC1918 private subnets to prevent 403 blocks.\n\
export no_proxy="$no_proxy,localhost,127.0.0.1,::1,.googleapis.com,.svc,metadata.google.internal,169.254.169.254"\n\
export NO_PROXY="$NO_PROXY,localhost,127.0.0.1,::1,.googleapis.com,.svc,metadata.google.internal,169.254.169.254"\n\
\n\
exec /opt/hermes/.venv/bin/hermes.real "$@"\n' > /usr/local/bin/hermes && \
    chmod +x /usr/local/bin/hermes && \
    ln -sf /opt/hermes/.venv/bin/pip3 /usr/local/bin/pip3


# 4. Clone and build mcp-remote proxy
RUN git clone https://github.com/bradhoekstra/mcp-remote.git /opt/mcp-remote && \
    cd /opt/mcp-remote && \
    git checkout 2ff8f16753d8c744e6982b99e6ed5ab62f44f311 && \
    npm install && \
    npm run build && \
    npm prune --production && \
    npm cache clean --force && \
    rm -rf .git

# 5. Create base defaults directory and copy shared defaults
# Also pre-create /opt/data for sandbox user runtime writes
RUN mkdir -p /opt/defaults /opt/data && chown -R sandbox:sandbox /opt/defaults /opt/data
COPY --chown=sandbox:sandbox deploy/shared/defaults/ /opt/defaults/
COPY --chown=sandbox:sandbox docs/glossary.md /opt/defaults/docs/


# 5.5. Install and enable hermes-otel plugin in /opt/defaults
RUN HOME=/opt/defaults HERMES_HOME=/opt/defaults /opt/hermes/.venv/bin/hermes.real plugins install briancaffey/hermes-otel && \
    HOME=/opt/defaults HERMES_HOME=/opt/defaults /opt/hermes/.venv/bin/hermes.real plugins enable hermes_otel && \
    /opt/hermes/.venv/bin/python3 -c "import yaml, pathlib; p = pathlib.Path('/opt/defaults/plugins/hermes_otel/config.yaml'); c = yaml.safe_load(p.read_text()) or {} if p.exists() else {}; c['backends'] = [{'name': 'gke-managed-otel', 'type': 'otlp', 'endpoint': 'http://opentelemetry-collector.gke-managed-otel.svc.cluster.local:4318/v1/traces'}]; p.write_text(yaml.safe_dump(c))"

# Copy shared skills
COPY --chown=sandbox:sandbox agents/shared/skills/ /opt/hermes/skills/



# 5.8. Copy custom agent startup script
COPY deploy/shared/docker-entrypoint.sh /usr/local/bin/agent-entrypoint
RUN chmod 755 /usr/local/bin/agent-entrypoint

# 6. Expose ports shared by all agent gateways
EXPOSE 8642
EXPOSE 9119

# 7. Execution lifecycle
ENTRYPOINT ["/usr/local/bin/agent-entrypoint"]
CMD ["hermes", "gateway", "run"]



# --- DEVTEAM AGENT TARGET ---
FROM agent-base AS devteam

# Copy identity template and defaults from consolidated templates
COPY agents/devteam/SOUL.md /opt/defaults/SOUL.md
COPY agents/devteam/AGENTS.md /opt/defaults/AGENTS.md
COPY agents/devteam/IDENTITY.md /opt/defaults/IDENTITY.md
COPY agents/devteam/HEARTBEAT.md /opt/defaults/HEARTBEAT.md
COPY agents/devteam/cron/ /opt/defaults/cron/
COPY agents/devteam/procedures/ /opt/defaults/procedures/

# Copy devteam skills
COPY agents/devteam/skills/ /opt/hermes/skills/

# Normalize ownership and permissions
RUN chown -R sandbox:sandbox /opt/defaults /opt/hermes/skills && \
    find /opt/defaults -type d -exec chmod 755 {} + && \
    find /opt/defaults -type f -exec chmod 644 {} + && \
    find /opt/hermes/skills -type d -exec chmod 755 {} + && \
    find /opt/hermes/skills -type f -exec chmod 644 {} + && \
    find /opt/hermes/skills -path '*/scripts/*' -type f -exec chmod 755 {} + 2>/dev/null || true

USER sandbox



# --- OPERATOR AGENT TARGET ---
FROM agent-base AS operator

# Copy identity template and defaults from consolidated templates
COPY agents/operator/SOUL.md /opt/defaults/SOUL.md
COPY agents/operator/AGENTS.md /opt/defaults/AGENTS.md
COPY agents/operator/IDENTITY.md /opt/defaults/IDENTITY.md
COPY agents/operator/HEARTBEAT.md /opt/defaults/HEARTBEAT.md
COPY agents/operator/cron/ /opt/defaults/cron/
COPY agents/operator/procedures/ /opt/defaults/procedures/

# Copy operator skills
COPY agents/operator/skills/ /opt/hermes/skills/

# Normalize ownership and permissions
RUN chown -R sandbox:sandbox /opt/defaults /opt/hermes/skills && \
    find /opt/defaults -type d -exec chmod 755 {} + && \
    find /opt/defaults -type f -exec chmod 644 {} + && \
    find /opt/hermes/skills -type d -exec chmod 755 {} + && \
    find /opt/hermes/skills -type f -exec chmod 644 {} + && \
    find /opt/hermes/skills -path '*/scripts/*' -type f -exec chmod 755 {} + 2>/dev/null || true

USER sandbox



# --- PLATFORM AGENT TARGET ---
FROM agent-base AS platform

# Setup platform specific defaults from consolidated configs
COPY agents/platform/cron/ /opt/defaults/cron/
COPY agents/platform/scripts/ /opt/defaults/scripts/
COPY agents/platform/governance/ /opt/defaults/governance/
COPY agents/platform/SOUL.md /opt/defaults/SOUL.md
COPY agents/platform/AGENTS.md /opt/defaults/AGENTS.md
COPY agents/platform/ROUTING.md /opt/defaults/ROUTING.md

# Copy platform skills
COPY agents/platform/skills/ /opt/hermes/skills/

# Copy platform defaults configuration as temp file
COPY agents/platform/config.yaml /tmp/platform-config.yaml

# Copy devteam deployment template
COPY deploy/kustomize/devteam/deployment.yaml /opt/defaults/templates/devteam/deployment.yaml

# Copy operator deployment template
COPY deploy/kustomize/operator/deployment.yaml /opt/defaults/templates/operator/deployment.yaml

# Copy config merge helper script
COPY deploy/docker/merge_configs.py /tmp/merge_configs.py

# Dynamically merge config.yamls using helper script
RUN /opt/hermes/.venv/bin/python3 /tmp/merge_configs.py /opt/defaults/config.yaml /tmp/platform-config.yaml && \
    rm -f /tmp/merge_configs.py /tmp/platform-config.yaml

# Normalize ownership and permissions at the end of build
RUN chown -R sandbox:sandbox /opt/defaults /opt/hermes/skills && \
    find /opt/defaults -type d -exec chmod 755 {} + && \
    find /opt/defaults -type f -exec chmod 644 {} + && \
    find /opt/hermes/skills -type d -exec chmod 755 {} + && \
    find /opt/hermes/skills -type f -exec chmod 644 {} + && \
    find /opt/defaults/scripts -type f -exec chmod 755 {} + 2>/dev/null && \
    find /opt/hermes/skills -path '*/scripts/*' -type f -exec chmod 755 {} + 2>/dev/null || true

USER sandbox
