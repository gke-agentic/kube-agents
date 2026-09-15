{{/*
Chart name and version, as the helm.sh/chart label value.
*/}}
{{- define "kube-agents.chart" -}}
{{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to every rendered object.

part-of is a constant, not a template value: it is the key the project-wide
footprint query selects on (-l app.kubernetes.io/part-of=kube-agents), so an
object that renders without it is invisible to every doc'd cleanup and audit
command. See the Resource labels reference page for the contract this shares
with the operator, the kustomizations, and the provisioner.
*/}}
{{- define "kube-agents.labels" -}}
helm.sh/chart: {{ include "kube-agents.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: kube-agents
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}

{{/*
The registry prefix images built from this repo resolve under, or "" to leave
them on their public defaults. Takes the root context.
*/}}
{{- define "kube-agents.imageRegistry" -}}
{{- (.Values.global | default dict).imageRegistry | default "" | trimSuffix "/" -}}
{{- end }}

{{/*
The same for images this project does not build (LiteLLM, fluent-bit). Falls
back to imageRegistry, since a single-prefix mirror is the common case and a
chart that mirrored only its own images would render a half-mirrored install —
the operator handing its managed pods public references after `helm install`
reported success.

This deliberately does NOT match third_party_registry_prefix in
scripts/installer/common.sh, which requires THIRD_PARTY_REGISTRY_PREFIX
explicitly. The asymmetry is about history, not preference: REGISTRY_PREFIX
shipped before this inventory existed and has always meant "the registry
holding the images this project builds", so widening it would redirect working
installs to images their mirror was never given. global.imageRegistry is new
here and carries no such promise, so it can take the safer default.

Takes the root context.
*/}}
{{- define "kube-agents.thirdPartyImageRegistry" -}}
{{- $g := .Values.global | default dict -}}
{{- $g.thirdPartyImageRegistry | default $g.imageRegistry | default "" | trimSuffix "/" -}}
{{- end }}

{{/*
One global.imagePullSecrets entry, as a Secret name.

Both spellings are accepted: the bare name, so a single secret is reachable
with --set global.imagePullSecrets[0]=regcred, and the {name: x} map that
Kubernetes' own PodSpec and most charts' global.imagePullSecrets take. The map
is the shape people write first, and rendering one straight into a value gives
the Secret name "map[name:regcred]" -- which the API server accepts, the
kubelet cannot find, and nothing anywhere reports as wrong. Anything else stops
the render, because the alternative is the same silent failure by another
route.

Takes one entry, not the root context.
*/}}
{{- define "kube-agents.imagePullSecretName" -}}
{{- if kindIs "string" . -}}
{{ required "global.imagePullSecrets: an entry cannot be an empty Secret name" . }}
{{- else if kindIs "map" . -}}
{{ required (printf "global.imagePullSecrets: a map entry needs a non-empty `name`; this one has keys [%s]" (join " " (keys .))) .name }}
{{- else -}}
{{ fail (printf "global.imagePullSecrets entries must be a Secret name or {name: <secret>}, got a %s" (kindOf .)) }}
{{- end -}}
{{- end }}

{{/*
The pod-level imagePullSecrets block, or nothing at all when
global.imagePullSecrets is empty.

Returns the whole block including its key, so callers write
`{{- with (include "kube-agents.imagePullSecrets" .) }}{{ . | nindent N }}{{- end }}`
and an unset value adds no stray blank line. Same contract as
kube-agents.compactFields, and the same reason: every pod spec the chart
renders and the PlatformAgent CR have to agree on this, and a hand-written `if`
at each of them is one place for the next reader to forget.

Takes the root context.
*/}}
{{- define "kube-agents.imagePullSecrets" -}}
{{- with (.Values.global | default dict).imagePullSecrets -}}
imagePullSecrets:
{{- range . }}
  - name: {{ include "kube-agents.imagePullSecretName" . | quote }}
{{- end }}
{{- end }}
{{- end }}

{{/*
The same names, comma-joined for the operator's IMAGE_PULL_SECRETS env var, or
the empty string when there are none -- falsy, so callers can `with` it.

Takes the root context.
*/}}
{{- define "kube-agents.imagePullSecretNames" -}}
{{- $names := list -}}
{{- range (.Values.global | default dict).imagePullSecrets -}}
{{- $names = append $names (include "kube-agents.imagePullSecretName" .) -}}
{{- end -}}
{{- join "," $names -}}
{{- end }}

{{/*
Rewrite an image repository onto a registry prefix, keeping only the trailing
image name: quay.io/jetstack/cert-manager-webhook under "reg.example.com/m"
becomes reg.example.com/m/cert-manager-webhook. That flat layout is what
scripts/mirror_images.sh writes and what the operator assumes when it derives
the credential-proxy reference from the agent one. An empty registry returns
the repository untouched, so a default install renders byte-identically.

The trailing segment is a stand-in for the real rule. mirror_images.sh names
each destination after the images.json entry's .name, and a chart cannot read
images.json at render time, so this reproduces it by convention rather than by
lookup. An image whose inventory name differs from its trailing segment
(hindsight-postgresql is docker.io/pgvector/pgvector) cannot use this helper —
kube-agents.thirdPartyImage below takes the real name explicitly. Check 3c in
hack/check-image-inventory.sh fails the build when a rendered mirror name is
not an inventory name, which is what keeps the shortcut safe.

Takes a dict: {repository, registry}. Returns the repository only — the
PlatformAgent CR carries repository and tag in separate fields, so joining
them here would not suit every caller.
*/}}
{{- define "kube-agents.imageRepository" -}}
{{- $registry := .registry | default "" | trimSuffix "/" -}}
{{- if $registry -}}
{{- printf "%s/%s" $registry (.repository | splitList "/" | last) -}}
{{- else -}}
{{- .repository -}}
{{- end -}}
{{- end }}

{{/*
A complete third-party image reference, reproducing third_party_image() from
scripts/installer/common.sh: mirrored installs pull <prefix>/<name>:<tag>
with any @sha256 digest dropped — `make mirror-images` pushes by tag, and the
copy's digest differs from the upstream one, so keeping it would break every
mirrored pull — while unmirrored installs pull the inventory's full pin,
digest and all.

`name` is the images.json entry name, which is what mirror_images.sh names the
destination; it defaults to the repository's trailing segment, the common case
where the two agree. Passing it explicitly is what lets an image like
hindsight-postgresql (docker.io/pgvector/pgvector) render correctly under a
mirror.

Takes a dict: {repository, tag, name (optional), root (the root context)}.
*/}}
{{- define "kube-agents.thirdPartyImage" -}}
{{- $registry := include "kube-agents.thirdPartyImageRegistry" .root -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry (.name | default (.repository | splitList "/" | last)) (.tag | splitList "@" | first) -}}
{{- else -}}
{{- printf "%s:%s" .repository .tag -}}
{{- end -}}
{{- end }}

{{/*
Whether the Hindsight memory store renders. hindsight.enabled is a tri-state:
true and false are answers, and null (the default) follows the agent's memory
provider — the providers that need the Hindsight API get it, everything else
does not, so an install cannot select hindsight memory and silently receive
no store.
*/}}
{{- define "kube-agents.hindsightEnabled" -}}
{{- $explicit := .Values.hindsight.enabled -}}
{{- if kindIs "invalid" $explicit -}}
{{- $provider := ((.Values.platformAgent.harness.memory | default dict).provider) | default "" -}}
{{- if or (eq $provider "kube_agents_memory") (eq $provider "hindsight") -}}
true
{{- end -}}
{{- else if $explicit -}}
true
{{- end -}}
{{- end }}

{{/*
The OTLP/HTTP collector base URL for the chart's own consumers (the LiteLLM exporter).

Unset means the GKE Managed OpenTelemetry collector, which is what these consumers have
always used. The operator has a richer answer available — it can discover a collector at
reconcile time — but Helm renders once, before any of that, so it keeps the historical
default rather than guessing.
*/}}
{{- define "kube-agents.otlpEndpoint" -}}
{{- .Values.telemetry.otlpEndpoint | default "http://opentelemetry-collector.gke-managed-otel.svc.cluster.local:4318" -}}
{{- end }}

{{/*
The namespace to open OTLP egress to, for the LiteLLM NetworkPolicy.

A namespaceSelector cannot be derived at reconcile time the way the agent's endpoint can:
it has to be right when the policy is applied. So it comes from telemetry.collectorNamespace
when given, and otherwise from the endpoint host, which is a cluster-local Service name in
the case this feature exists for (<svc>.<ns>.svc.cluster.local, or the shortened <svc>.<ns>).

Anything else — an external vendor endpoint, a bare hostname — has no namespace to open,
and what the static policy does then follows the operator's dynamic copy. With
litellm.otel on, this renders "" and the caller emits no OTLP rule: the exporter goes out
over the port-443 rule, and a made-up namespaceSelector would open 4317/4318 to a
namespace nothing exports to. With litellm.otel off (the default) there is no LiteLLM
exporter, and the rule keeps the shipping gke-managed-otel default rather than changing
a policy over an egress rule nothing uses.

The host is parsed the way the operator's otlpCollectorNamespace (k8s-operator,
platformagent_manifests.go) parses the same value when it builds the dynamic policy —
exact lowercase scheme prefixes, cut at the first "/", then at the first ":" — so the two
renders reach the same verdict about the same endpoint.

Only the static litellm-policy render calls this. On the default install the operator
owns the policy and resolves the namespace at reconcile time from the CR.
*/}}
{{- define "kube-agents.otlpCollectorNamespace" -}}
{{- if .Values.telemetry.collectorNamespace -}}
{{- .Values.telemetry.collectorNamespace -}}
{{- else if not .Values.telemetry.otlpEndpoint -}}
gke-managed-otel
{{- else -}}
{{- $host := .Values.telemetry.otlpEndpoint | trimPrefix "https://" | trimPrefix "http://" -}}
{{- $host = (splitList "/" $host | first) -}}
{{- $host = (splitList ":" $host | first) -}}
{{- $parts := splitList "." $host -}}
{{- /*
  Only two shapes are an in-cluster Service: exactly <svc>.<ns>, or <svc>.<ns>.svc[...].
  Anything with a third label that is not "svc" is a public DNS name, and reading its
  second label as a namespace would quietly open egress to a namespace named "vendor".
*/ -}}
{{- if or (eq (len $parts) 2) (and (ge (len $parts) 3) (eq (index $parts 2) "svc")) -}}
{{- index $parts 1 -}}
{{- else if not .Values.litellm.otel -}}
gke-managed-otel
{{- end -}}
{{- end -}}
{{- end }}

{{/*
Renders a dict of optional CR fields as YAML, dropping the ones left unset.

"Unset" is null or the empty string; `false` and `0` are values and survive,
which is the whole reason this exists — `with` and plain truthiness drop both,
and a boolean knob nobody can set to false is not a knob.

Returns the empty string when every field is unset, so a caller can write
`{{- with (include ...) }}` and have the PARENT block disappear too. That
coupling is the point: guarding a parent by hand means enumerating its children
in an `or`, and the failure mode when a later field is added to one list and not
the other is silence — the template still emits valid YAML, just without the
field somebody set.

Takes a dict of field name to value.
*/}}
{{- define "kube-agents.compactFields" -}}
{{- $out := dict -}}
{{- range $key, $value := . -}}
{{- if not (or (kindIs "invalid" $value) (and (kindIs "string" $value) (eq $value ""))) -}}
{{- $_ := set $out $key $value -}}
{{- end -}}
{{- end -}}
{{- if $out -}}
{{- toYaml $out -}}
{{- end -}}
{{- end }}

{{/*
The LiteLLM gateway config, mirroring
k8s-operator/config/integrations/litellm/base/config.yaml.

Defined once and consumed twice — as the ConfigMap body and as the input to the
Deployment's checksum annotation — because those two must not be able to
disagree. Hashing the inputs (provider, model, callbacks) instead of the output
was the earlier shape and it missed any edit to this template itself: the
ConfigMap changed, the checksum did not, the Deployment did not roll. The
gateway mounts this with subPath, and a subPath ConfigMap mount never receives
in-place updates, so the running pod would have kept the old file indefinitely.

Takes a dict of provider, model, callbacks.
*/}}
{{- define "kube-agents.litellmConfig" -}}
model_list:
  - model_name: model-default
    litellm_params:
      model: {{ printf "%s/%s" .provider .model }}
  - model_name: hermes-agent
    litellm_params:
      model: {{ printf "%s/%s" .provider .model }}
  - model_name: {{ .model }}
    litellm_params:
      model: {{ printf "%s/%s" .provider .model }}
litellm_settings:
  callbacks: {{ .callbacks }}
{{- /*
  Prompt caching. Kept identical to the kustomize base
  (k8s-operator/config/integrations/litellm/base/config.yaml) — see that file
  for why the breakpoints live here rather than in the agent's own config, and
  why non-Anthropic backends are unaffected.
*/}}
router_settings:
  default_litellm_params:
    cache_control_injection_points:
      - location: message
        role: system
        control:
          type: ephemeral
          ttl: 1h
      - location: message
        index: -3
      - location: message
        index: -1
{{- end }}

{{/*
Selector labels for the operator Deployment. Kept minimal and stable:
selectors are immutable once the Deployment exists.
*/}}
{{- define "kube-agents.operatorSelectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}-operator
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Admission-webhook object names, mirroring k8s-operator/config/webhook and
config/certmanager.

Defined here rather than inlined because four templates have to agree on them:
the Service the webhook configurations' clientConfig points at, the Certificate
whose dnsNames must match that Service, the Secret the Deployment mounts, and
the inject-ca-from annotation. A name that disagrees across any two of those
renders valid YAML and fails at admission time, which is the wrong place to find
out.

The webhook configurations are cluster-scoped, so they carry the namespace
component the chart already uses for the operator ClusterRole — two releases in
different namespaces would otherwise fight over one object, and the loser's
clientConfig would point every PlatformAgent admission in the cluster at the
wrong Service.
*/}}
{{- define "kube-agents.webhookServiceName" -}}
{{ .Release.Name }}-webhook-service
{{- end }}

{{- define "kube-agents.webhookCertificateName" -}}
{{ .Release.Name }}-serving-cert
{{- end }}

{{- define "kube-agents.webhookCertSecretName" -}}
{{ .Release.Name }}-webhook-certs
{{- end }}

{{- define "kube-agents.webhookConfigurationPrefix" -}}
{{ .Release.Name }}-{{ .Release.Namespace }}
{{- end }}

{{/*
Validates and resolves a Deployment's rollingUpdate fenceposts, returning a
YAML map with `maxSurge` and `maxUnavailable`. Callers parse the output with
`| fromYaml`.

Both fenceposts at zero leaves the Deployment no way to make progress and
the API server rejects it ("may not be 0 when maxSurge is 0"), so fail
the render rather than the apply. See values.yaml's rollingUpdate blocks.

A fencepost with no usable value takes the given default (defaultSurge,
defaultUnavailable) and renders explicitly. "No usable value" has to mean
the empty string as well as nil/invalid: `--set <scope>.maxUnavailable=` and
a values file's `maxUnavailable: ""` both reach here as an empty string,
which is a perfectly good `kind` and so survives a nil test. Rendering
either through would emit `maxUnavailable:` with nothing after it, and
Kubernetes then applies its own 25% default.

Both fields are IntOrString, which is what makes the zero test awkward:
`int` is cast.ToInt and reads "25%" as 0, so it would refuse a pair of
perfectly good percentages, while a list of literal spellings misses
"0.0". Compare numerically with the percent sign stripped — "0%"
resolves to 0 on the cluster, so it is the same misconfiguration spelled
differently. `float64` is cast.ToFloat64, which reports anything it
cannot parse as 0, so the numeric test is gated on both values actually
being numeric: without that, `maxSurge: abc` is refused as a zero and
the message names the wrong problem. Non-numeric input is left to the
API server, which is where it was rejected before this guard existed.

Takes a dict: {rollingUpdate, defaultSurge, defaultUnavailable, scope}.
*/}}
{{- define "kube-agents.rollingUpdateFenceposts" -}}
{{- $ru := .rollingUpdate | default dict -}}
{{- $surge := $ru.maxSurge -}}
{{- $unavail := $ru.maxUnavailable -}}
{{- $defaultSurge := .defaultSurge -}}
{{- if kindIs "invalid" $defaultSurge }}{{- $defaultSurge = 1 }}{{- end -}}
{{- $defaultUnavail := .defaultUnavailable -}}
{{- if kindIs "invalid" $defaultUnavail }}{{- $defaultUnavail = 0 }}{{- end -}}
{{- if or (kindIs "invalid" $surge) (eq (toString $surge) "") }}{{- $surge = $defaultSurge }}{{- end -}}
{{- if or (kindIs "invalid" $unavail) (eq (toString $unavail) "") }}{{- $unavail = $defaultUnavail }}{{- end -}}
{{- $surgeNum := trimSuffix "%" (trim (toString $surge)) -}}
{{- $unavailNum := trimSuffix "%" (trim (toString $unavail)) -}}
{{- $numeric := "^[0-9]+(\\.[0-9]+)?$" -}}
{{- if and (regexMatch $numeric $surgeNum) (regexMatch $numeric $unavailNum) -}}
{{- if and (eq (float64 $surgeNum) 0.0) (eq (float64 $unavailNum) 0.0) -}}
{{- fail (printf "%s: maxSurge (%v) and maxUnavailable (%v) may not both be zero — the Deployment would have no way to make progress, and the API server rejects it." .scope $surge $unavail) -}}
{{- end -}}
{{- end -}}
maxSurge: {{ $surge }}
maxUnavailable: {{ $unavail }}
{{- end }}

{{/*
Resource parsing helpers for quota preflight (#749).
Converts Kubernetes quantities to canonical integer units:
- CPU: millicores (e.g. "500m" -> 500, "1" -> 1000, "1.5" -> 1500)
- Memory / Storage: bytes (e.g. "128Mi" -> 134217728, "2Gi" -> 2147483648)

Both fail the render on a quantity they cannot parse rather than returning a number.
An earlier version fell through to `int64`, which yields 0 for anything it does not
understand: a `1Pi` quota then read as `hard 0` and the release was refused with a
message describing a cluster that does not exist. A quantity this cannot read is a bug
in this helper, and saying so is the only honest outcome.
*/}}
{{- define "kube-agents.parseCpuMillis" -}}
{{- $raw := trim (toString .) -}}
{{- $numeric := "^[0-9]+(\\.[0-9]+)?([eE][-+]?[0-9]+)?$" -}}
{{- if or (eq $raw "") (eq $raw "<nil>") -}}
0
{{- else if hasSuffix "m" $raw -}}
{{- $n := trimSuffix "m" $raw -}}
{{- if not (regexMatch $numeric $n) -}}
{{- fail (printf "quota preflight: cannot parse CPU quantity %q — set quotaPreflight.enabled=false to bypass, and please report it." $raw) -}}
{{- end -}}
{{- float64 $n | int64 -}}
{{- else -}}
{{- if not (regexMatch $numeric $raw) -}}
{{- fail (printf "quota preflight: cannot parse CPU quantity %q — set quotaPreflight.enabled=false to bypass, and please report it." $raw) -}}
{{- end -}}
{{- mulf (float64 $raw) 1000 | int64 -}}
{{- end -}}
{{- end }}

{{- define "kube-agents.parseBytes" -}}
{{- $raw := trim (toString .) -}}
{{- $numeric := "^[0-9]+(\\.[0-9]+)?([eE][-+]?[0-9]+)?$" -}}
{{- $binary := dict "Ki" 1024.0 "Mi" 1048576.0 "Gi" 1073741824.0 "Ti" 1099511627776.0 "Pi" 1125899906842624.0 "Ei" 1152921504606846976.0 -}}
{{- $decimal := dict "k" 1000.0 "M" 1000000.0 "G" 1000000000.0 "T" 1000000000000.0 "P" 1000000000000000.0 "E" 1000000000000000000.0 -}}
{{- if or (eq $raw "") (eq $raw "<nil>") -}}
0
{{- else -}}
{{- $out := "" -}}
{{- range $unit, $mult := $binary -}}
{{- if and (eq $out "") (hasSuffix $unit $raw) -}}
{{- $n := trimSuffix $unit $raw -}}
{{- if not (regexMatch $numeric $n) -}}
{{- fail (printf "quota preflight: cannot parse quantity %q (memory, storage or count) — set quotaPreflight.enabled=false to bypass, and please report it." $raw) -}}
{{- end -}}
{{- $out = mulf (float64 $n) $mult | int64 | toString -}}
{{- end -}}
{{- end -}}
{{- if eq $out "" -}}
{{- range $unit, $mult := $decimal -}}
{{- if and (eq $out "") (hasSuffix $unit $raw) -}}
{{- $n := trimSuffix $unit $raw -}}
{{- if not (regexMatch $numeric $n) -}}
{{- fail (printf "quota preflight: cannot parse quantity %q (memory, storage or count) — set quotaPreflight.enabled=false to bypass, and please report it." $raw) -}}
{{- end -}}
{{- $out = mulf (float64 $n) $mult | int64 | toString -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if eq $out "" -}}
{{- if not (regexMatch $numeric $raw) -}}
{{- fail (printf "quota preflight: cannot parse quantity %q (memory, storage or count) — set quotaPreflight.enabled=false to bypass, and please report it." $raw) -}}
{{- end -}}
{{- $out = float64 $raw | int64 | toString -}}
{{- end -}}
{{- $out -}}
{{- end -}}
{{- end }}

{{/*
Count quotas (`pods`, `persistentvolumeclaims`) go through the same parser.

They are not plain integers on the wire. The API server round-trips every quota value
through resource.Quantity and writes back the canonical form, so a namespace created
with `pods: 1000` is read back as `pods: "1k"`. Sprig's `int64` is `cast.ToInt64`, which
answers 0 for a string it cannot parse rather than failing — so an earlier version read
that quota as `hard 0`, refused the release for a shortfall that did not exist, and
printed a patch lowering the namespace to 7 pods for whoever followed the instructions.
parseBytes already reads the decimal-SI suffixes this needs, and fails loudly on the rest.
*/}}
{{- define "kube-agents.parseCount" -}}
{{- include "kube-agents.parseBytes" . -}}
{{- end }}

{{/*
Format helpers for friendly error display and patch generation:
- CPU: converts millicores to e.g. "10000m" (or "10" if exact integer cores)
- Memory / Storage: converts bytes to Mi / Gi
scripts/generate_chart_footprint.py has the same two functions, so the numbers the
preflight prints and the numbers in footprint.yaml are written the same way.
*/}}
{{- define "kube-agents.formatCpu" -}}
{{- $m := int64 . -}}
{{- if and (gt $m 0) (eq (mod $m 1000) 0) -}}
{{- printf "%d" (div $m 1000) -}}
{{- else -}}
{{- printf "%dm" $m -}}
{{- end -}}
{{- end }}

{{- define "kube-agents.formatBytes" -}}
{{- $b := int64 . -}}
{{- if and (gt $b 0) (eq (mod $b 1073741824) 0) -}}
{{- printf "%dGi" (div $b 1073741824) -}}
{{- else if and (gt $b 0) (eq (mod $b 1048576) 0) -}}
{{- printf "%dMi" (div $b 1048576) -}}
{{- else -}}
{{- printf "%d" $b -}}
{{- end -}}
{{- end }}

{{/*
Two more byte formatters, for the quota diagnosis rather than for footprint.yaml.

formatBytes above only names a unit when the value divides exactly, and falls back to a
bare byte count otherwise. That is right for footprint.yaml, whose numbers are always
Mi-aligned, and wrong in the failure message: a namespace whose quota is written in
decimal SI (`requests.memory: 10G`) turns every figure into an eleven-digit byte count,
which is the opposite of the legible diagnosis this check exists to give.

Rounding in a patch value is not free, so the direction is chosen per use:

- formatBytesCeil rounds UP to whole Mi, and sizes the remediation patch. Rounding down
  would print a patch that is short of what the release needs, which is worse than an
  ugly number: the operator runs it and the install still fails.
- formatBytesApprox rounds toward zero and marks the result `~`, and is display-only.
  Nothing is computed from it, and the `~` keeps it from being read as exact.
*/}}
{{- define "kube-agents.formatBytesCeil" -}}
{{- $b := int64 . -}}
{{- if and (gt $b 0) (eq (mod $b 1048576) 0) -}}
{{- include "kube-agents.formatBytes" $b -}}
{{- else if le $b 0 -}}
{{- printf "%d" $b -}}
{{- else -}}
{{- printf "%dMi" (div (add $b 1048575) 1048576) -}}
{{- end -}}
{{- end }}

{{- define "kube-agents.formatBytesApprox" -}}
{{- $b := int64 . -}}
{{- if and (gt $b 0) (eq (mod $b 1048576) 0) -}}
{{- include "kube-agents.formatBytes" $b -}}
{{- else if eq $b 0 -}}
0
{{- else -}}
{{- printf "~%dMi" (div $b 1048576) -}}
{{- end -}}
{{- end }}

{{/*
Preflight validation against namespace ResourceQuotas (#749).

Split into three templates so the parts that need no cluster can be tested without one:

- kube-agents.quotaRequirements — totals what the release needs, as JSON. Pure function of
  the values and footprint.yaml.
- kube-agents.quotaCheckItems — compares those totals against a list of ResourceQuota
  objects and fails the render on a shortfall. Takes the list as an argument.
- kube-agents.quotaPreflight — the entry point: looks the quotas up, then calls the two above.

Only the last one touches the cluster, so tests/test_quota_preflight.py can drive the other
two with synthetic quotas and assert the arithmetic and the pass/fail decision offline.

Fails the render if:
- hard < required (the quota cannot fit the release even if empty)
- OR (hard - used) < required AND .Release.IsInstall (on a fresh install, remaining headroom
  is insufficient). Install-only on purpose: on upgrade the release's own pods are already
  counted in `used`, so subtracting them again would refuse every upgrade of a release that
  exactly fits its quota.

Quota keys understood: CPU, memory and ephemeral-storage (requests and limits), pods,
persistentvolumeclaims and requests.storage. Keys outside that set (services, secrets, other
count/<resource>) are not modelled, and are skipped rather than guessed at.

Inert when lookup returns empty — `helm template` without a cluster, or a namespace with no
ResourceQuota at all.

It is NOT inert when the installing identity cannot read ResourceQuotas. Helm's `lookup`
swallows a NotFound and returns nothing; every other API error, a 403 on
`list resourcequotas` among them, comes back as a template error and aborts the render. So
the check needs `get`/`list` on `resourcequotas` in the release namespace, and an identity
without it installs with `--set quotaPreflight.enabled=false`. Nothing here can soften that:
a Go template cannot catch the error `lookup` raises.
*/}}
{{- define "kube-agents.quotaRequirements" -}}
{{- $footprint := .Files.Get "files/footprint.yaml" | fromYaml -}}
{{- /* The footprint is the only source for the operator-rendered pods, which are most of
       the release. If it is missing or unparseable every one of them silently counts as
       zero and the preflight waves through a quota that cannot fit the release — the exact
       failure it exists to prevent, now with a green light in front of it. */ -}}
{{- if not (index $footprint "operatorRendered") -}}
  {{- fail "quota preflight: footprint.yaml is missing or unreadable in the chart, so the operator-rendered pods cannot be sized. Reinstall from an intact chart, or set quotaPreflight.enabled=false to skip the check." -}}
{{- end -}}
{{- $op := (index $footprint "operatorRendered") | default dict -}}

{{- $reqPods := 0 -}}
{{- $reqCpu := 0 -}}
{{- $limCpu := 0 -}}
{{- $reqMem := 0 -}}
{{- $limMem := 0 -}}
{{- $reqEph := 0 -}}
{{- $limEph := 0 -}}
{{- $reqPvc := 0 -}}
{{- $reqStorage := 0 -}}

{{- /* Largest single pod among the workloads that roll with a surge Pod. Used only to size
       the remediation patch, never the pass/fail threshold: a quota raised to exactly
       used+required fits the release at rest and then stalls its first rollout, which is the
       failure values.yaml warns about under hindsight.api.rollingUpdate. Rollouts are
       per-workload, so room for one surge Pod at a time is enough. */ -}}
{{- $surgeCpuReq := 0 -}}
{{- $surgeCpuLim := 0 -}}
{{- $surgeMemReq := 0 -}}
{{- $surgeMemLim := 0 -}}

{{- /* Operator */ -}}
{{- if .Values.operator.enabled -}}
  {{- $replicas := .Values.operator.replicaCount | default 1 | int64 -}}
  {{- $cReqCpu := include "kube-agents.parseCpuMillis" .Values.operator.resources.requests.cpu | int64 -}}
  {{- $cLimCpu := include "kube-agents.parseCpuMillis" .Values.operator.resources.limits.cpu | int64 -}}
  {{- $cReqMem := include "kube-agents.parseBytes" .Values.operator.resources.requests.memory | int64 -}}
  {{- $cLimMem := include "kube-agents.parseBytes" .Values.operator.resources.limits.memory | int64 -}}
  {{- $reqPods = add $reqPods $replicas -}}
  {{- $reqCpu = add $reqCpu (mul $cReqCpu $replicas) -}}
  {{- $limCpu = add $limCpu (mul $cLimCpu $replicas) -}}
  {{- $reqMem = add $reqMem (mul $cReqMem $replicas) -}}
  {{- $limMem = add $limMem (mul $cLimMem $replicas) -}}
  {{- $surgeCpuReq = max $surgeCpuReq $cReqCpu -}}
  {{- $surgeCpuLim = max $surgeCpuLim $cLimCpu -}}
  {{- $surgeMemReq = max $surgeMemReq $cReqMem -}}
  {{- $surgeMemLim = max $surgeMemLim $cLimMem -}}
{{- end -}}

{{- /* LiteLLM */ -}}
{{- if .Values.litellm.enabled -}}
  {{- $replicas := .Values.litellm.replicaCount | default 1 | int64 -}}
  {{- $cReqCpu := include "kube-agents.parseCpuMillis" .Values.litellm.resources.requests.cpu | int64 -}}
  {{- $cLimCpu := include "kube-agents.parseCpuMillis" .Values.litellm.resources.limits.cpu | int64 -}}
  {{- $cReqMem := include "kube-agents.parseBytes" .Values.litellm.resources.requests.memory | int64 -}}
  {{- $cLimMem := include "kube-agents.parseBytes" .Values.litellm.resources.limits.memory | int64 -}}
  {{- $reqPods = add $reqPods $replicas -}}
  {{- $reqCpu = add $reqCpu (mul $cReqCpu $replicas) -}}
  {{- $limCpu = add $limCpu (mul $cLimCpu $replicas) -}}
  {{- $reqMem = add $reqMem (mul $cReqMem $replicas) -}}
  {{- $limMem = add $limMem (mul $cLimMem $replicas) -}}
  {{- $surgeCpuReq = max $surgeCpuReq $cReqCpu -}}
  {{- $surgeCpuLim = max $surgeCpuLim $cLimCpu -}}
  {{- $surgeMemReq = max $surgeMemReq $cReqMem -}}
  {{- $surgeMemLim = max $surgeMemLim $cLimMem -}}
{{- end -}}

{{- /* Hindsight: the API Deployment and the postgresql StatefulSet. */ -}}
{{- if include "kube-agents.hindsightEnabled" . -}}
  {{- $reqPods = add $reqPods 2 -}}
  {{- $aReqCpu := include "kube-agents.parseCpuMillis" .Values.hindsight.api.resources.requests.cpu | int64 -}}
  {{- $aLimCpu := include "kube-agents.parseCpuMillis" .Values.hindsight.api.resources.limits.cpu | int64 -}}
  {{- $aReqMem := include "kube-agents.parseBytes" .Values.hindsight.api.resources.requests.memory | int64 -}}
  {{- $aLimMem := include "kube-agents.parseBytes" .Values.hindsight.api.resources.limits.memory | int64 -}}
  {{- $reqCpu = add $reqCpu $aReqCpu -}}
  {{- $limCpu = add $limCpu $aLimCpu -}}
  {{- $reqMem = add $reqMem $aReqMem -}}
  {{- $limMem = add $limMem $aLimMem -}}
  {{- $surgeCpuReq = max $surgeCpuReq $aReqCpu -}}
  {{- $surgeCpuLim = max $surgeCpuLim $aLimCpu -}}
  {{- $surgeMemReq = max $surgeMemReq $aReqMem -}}
  {{- $surgeMemLim = max $surgeMemLim $aLimMem -}}
  {{- $reqCpu = add $reqCpu (include "kube-agents.parseCpuMillis" .Values.hindsight.postgresql.resources.requests.cpu | int64) -}}
  {{- $limCpu = add $limCpu (include "kube-agents.parseCpuMillis" .Values.hindsight.postgresql.resources.limits.cpu | int64) -}}
  {{- $reqMem = add $reqMem (include "kube-agents.parseBytes" .Values.hindsight.postgresql.resources.requests.memory | int64) -}}
  {{- $limMem = add $limMem (include "kube-agents.parseBytes" .Values.hindsight.postgresql.resources.limits.memory | int64) -}}
  {{- /* The same key templates/hindsight.yaml renders the volumeClaimTemplate request
         from. values.schema.json closes hindsight.postgresql to image, resources and
         storage, so there is no key to fall back to and no default to guard: a missing
         one is a schema violation the render has already rejected. */ -}}
  {{- $reqPvc = add $reqPvc 1 -}}
  {{- $reqStorage = add $reqStorage (include "kube-agents.parseBytes" .Values.hindsight.postgresql.storage | int64) -}}
{{- end -}}

{{- /* GitHub Minter */ -}}
{{- if .Values.githubMinter.enabled -}}
  {{- $replicas := .Values.githubMinter.replicaCount | default 1 | int64 -}}
  {{- $cReqCpu := include "kube-agents.parseCpuMillis" .Values.githubMinter.resources.requests.cpu | int64 -}}
  {{- $cLimCpu := include "kube-agents.parseCpuMillis" .Values.githubMinter.resources.limits.cpu | int64 -}}
  {{- $cReqMem := include "kube-agents.parseBytes" .Values.githubMinter.resources.requests.memory | int64 -}}
  {{- $cLimMem := include "kube-agents.parseBytes" .Values.githubMinter.resources.limits.memory | int64 -}}
  {{- $reqPods = add $reqPods $replicas -}}
  {{- $reqCpu = add $reqCpu (mul $cReqCpu $replicas) -}}
  {{- $limCpu = add $limCpu (mul $cLimCpu $replicas) -}}
  {{- $reqMem = add $reqMem (mul $cReqMem $replicas) -}}
  {{- $limMem = add $limMem (mul $cLimMem $replicas) -}}
  {{- $surgeCpuReq = max $surgeCpuReq $cReqCpu -}}
  {{- $surgeCpuLim = max $surgeCpuLim $cLimCpu -}}
  {{- $surgeMemReq = max $surgeMemReq $cReqMem -}}
  {{- $surgeMemLim = max $surgeMemLim $cLimMem -}}
{{- end -}}

{{- /* Operator-rendered workloads (footprint.yaml) */ -}}
{{- if .Values.platformAgent.enabled -}}
  {{- /* The agent pod is the one operator-rendered workload that scales: the gateway
         Deployment takes spec.replicas from availability.replicas, while the shell
         StatefulSet and the credential proxy stay at 1 (see the platformagent-ha golden,
         where the gateway goes to 3 and the other two do not). The footprint records one
         pod's worth, so it is multiplied here — without this an HA install passes the
         check and then leaves its extra replicas Pending, which is the failure this
         whole template exists to prevent. `null` means the operator's own default of 1. */ -}}
  {{- $agentReplicas := (((.Values.platformAgent.deployment | default dict).availability | default dict).replicas) | default 1 | int64 -}}
  {{- $base := (index $op "agentPod" "base") | default dict -}}
  {{- $podReqCpu := $base.cpuMillisRequest | default 0 | int64 -}}
  {{- $podLimCpu := $base.cpuMillisLimit | default 0 | int64 -}}
  {{- $podReqMem := $base.memoryBytesRequest | default 0 | int64 -}}
  {{- $podLimMem := $base.memoryBytesLimit | default 0 | int64 -}}
  {{- $podReqEph := $base.ephemeralStorageBytesRequest | default 0 | int64 -}}
  {{- $podLimEph := $base.ephemeralStorageBytesLimit | default 0 | int64 -}}

  {{- /* The dashboard is another container in the agent pod rather than a pod of its own,
         so it scales with the same replica count and adds no pod. Its flag is
         harness.hermes.dashboardEnabled; reading it one level up at harness.dashboardEnabled
         matches nothing, leaves this branch dead, and counts the dashboard even when it is
         switched off. `null` there means "no opinion", so the CRD default (true) applies. */ -}}
  {{- $hermes := (index (.Values.platformAgent.harness | default dict) "hermes") | default dict -}}
  {{- $dashEnabled := true -}}
  {{- if kindIs "bool" (index $hermes "dashboardEnabled") -}}
    {{- $dashEnabled = index $hermes "dashboardEnabled" -}}
  {{- end -}}
  {{- if $dashEnabled -}}
    {{- $dash := (index $op "agentPod" "dashboard") | default dict -}}
    {{- $podReqCpu = add $podReqCpu ($dash.cpuMillisRequest | default 0 | int64) -}}
    {{- $podLimCpu = add $podLimCpu ($dash.cpuMillisLimit | default 0 | int64) -}}
    {{- $podReqMem = add $podReqMem ($dash.memoryBytesRequest | default 0 | int64) -}}
    {{- $podLimMem = add $podLimMem ($dash.memoryBytesLimit | default 0 | int64) -}}
    {{- $podReqEph = add $podReqEph ($dash.ephemeralStorageBytesRequest | default 0 | int64) -}}
    {{- $podLimEph = add $podLimEph ($dash.ephemeralStorageBytesLimit | default 0 | int64) -}}
  {{- end -}}

  {{- $reqPods = add $reqPods (mul ($base.pods | default 1 | int64) $agentReplicas) -}}
  {{- $reqCpu = add $reqCpu (mul $podReqCpu $agentReplicas) -}}
  {{- $limCpu = add $limCpu (mul $podLimCpu $agentReplicas) -}}
  {{- $reqMem = add $reqMem (mul $podReqMem $agentReplicas) -}}
  {{- $limMem = add $limMem (mul $podLimMem $agentReplicas) -}}
  {{- $reqEph = add $reqEph (mul $podReqEph $agentReplicas) -}}
  {{- $limEph = add $limEph (mul $podLimEph $agentReplicas) -}}
  {{- $surgeCpuReq = max $surgeCpuReq $podReqCpu -}}
  {{- $surgeCpuLim = max $surgeCpuLim $podLimCpu -}}
  {{- $surgeMemReq = max $surgeMemReq $podReqMem -}}
  {{- $surgeMemLim = max $surgeMemLim $podLimMem -}}

  {{- $shell := (index $op "shellSandbox") | default dict -}}
  {{- $reqPods = add $reqPods ($shell.pods | default 1 | int64) -}}
  {{- $reqCpu = add $reqCpu ($shell.cpuMillisRequest | default 0 | int64) -}}
  {{- $limCpu = add $limCpu ($shell.cpuMillisLimit | default 0 | int64) -}}
  {{- $reqMem = add $reqMem ($shell.memoryBytesRequest | default 0 | int64) -}}
  {{- $limMem = add $limMem ($shell.memoryBytesLimit | default 0 | int64) -}}
  {{- $reqEph = add $reqEph ($shell.ephemeralStorageBytesRequest | default 0 | int64) -}}
  {{- $limEph = add $limEph ($shell.ephemeralStorageBytesLimit | default 0 | int64) -}}

  {{- $cred := (index $op "credentialProxy") | default dict -}}
  {{- $reqPods = add $reqPods ($cred.pods | default 1 | int64) -}}
  {{- $reqCpu = add $reqCpu ($cred.cpuMillisRequest | default 0 | int64) -}}
  {{- $limCpu = add $limCpu ($cred.cpuMillisLimit | default 0 | int64) -}}
  {{- $reqMem = add $reqMem ($cred.memoryBytesRequest | default 0 | int64) -}}
  {{- $limMem = add $limMem ($cred.memoryBytesLimit | default 0 | int64) -}}
  {{- $reqEph = add $reqEph ($cred.ephemeralStorageBytesRequest | default 0 | int64) -}}
  {{- $limEph = add $limEph ($cred.ephemeralStorageBytesLimit | default 0 | int64) -}}

  {{- /* Claims are release-scoped rather than per-replica, so they are not multiplied. */ -}}
  {{- $storage := (index $op "storage") | default dict -}}
  {{- $reqPvc = add $reqPvc ($storage.persistentVolumeClaims | default 0 | int64) -}}
  {{- $reqStorage = add $reqStorage ($storage.storageBytesRequest | default 0 | int64) -}}
{{- end -}}

{{- dict
      "pods" $reqPods
      "requestsCpu" $reqCpu "limitsCpu" $limCpu
      "requestsMemory" $reqMem "limitsMemory" $limMem
      "requestsEphemeral" $reqEph "limitsEphemeral" $limEph
      "persistentVolumeClaims" $reqPvc "requestsStorage" $reqStorage
      "surgeRequestsCpu" $surgeCpuReq "surgeLimitsCpu" $surgeCpuLim
      "surgeRequestsMemory" $surgeMemReq "surgeLimitsMemory" $surgeMemLim
   | toJson -}}
{{- end }}

{{- define "kube-agents.quotaCheckItems" -}}
{{- $ctx := .ctx -}}
{{- $r := .required -}}
{{- range $quota := .items -}}
  {{- $spec := index $quota "spec" | default dict -}}
  {{- $scopes := index $spec "scopes" -}}
  {{- $scopeSelector := index $spec "scopeSelector" -}}
  {{- if or $scopes $scopeSelector -}}
    {{- /* Scoped quota: it applies to a subset of pods this template cannot identify, so
           comparing the whole release against it would be wrong in both directions. */ -}}
  {{- else -}}
    {{- $hard := index $spec "hard" | default dict -}}
    {{- $status := index $quota "status" | default dict -}}
    {{- $used := index $status "used" | default dict -}}
    {{- $shortfalls := list -}}
    {{- $patchEntries := list -}}

    {{- range $key, $hardRaw := $hard -}}
      {{- $req := 0 -}}
      {{- /* Named $surgeRoom rather than the fencepost name used by
             kube-agents.rollingUpdateFenceposts: tests/test_deployments_rollout_quota.py
             scans this whole file for an unguarded reassignment of that name, which would be
             a rollingUpdate fencepost pinned for every install. This is a different quantity
             — the headroom the remediation patch leaves — and sharing the name would make
             that check unreadable. */ -}}
      {{- $surgeRoom := 0 -}}
      {{- $isCpu := false -}}
      {{- $isBytes := false -}}
      {{- $isCount := false -}}

      {{- if eq $key "limits.cpu" -}}
        {{- $req = int64 $r.limitsCpu -}}
        {{- $surgeRoom = int64 $r.surgeLimitsCpu -}}
        {{- $isCpu = true -}}
      {{- else if or (eq $key "requests.cpu") (eq $key "cpu") -}}
        {{- $req = int64 $r.requestsCpu -}}
        {{- $surgeRoom = int64 $r.surgeRequestsCpu -}}
        {{- $isCpu = true -}}
      {{- else if eq $key "limits.memory" -}}
        {{- $req = int64 $r.limitsMemory -}}
        {{- $surgeRoom = int64 $r.surgeLimitsMemory -}}
        {{- $isBytes = true -}}
      {{- else if or (eq $key "requests.memory") (eq $key "memory") -}}
        {{- $req = int64 $r.requestsMemory -}}
        {{- $surgeRoom = int64 $r.surgeRequestsMemory -}}
        {{- $isBytes = true -}}
      {{- else if eq $key "limits.ephemeral-storage" -}}
        {{- $req = int64 $r.limitsEphemeral -}}
        {{- $isBytes = true -}}
      {{- else if or (eq $key "requests.ephemeral-storage") (eq $key "ephemeral-storage") -}}
        {{- $req = int64 $r.requestsEphemeral -}}
        {{- $isBytes = true -}}
      {{- else if or (eq $key "requests.storage") (eq $key "storage") -}}
        {{- $req = int64 $r.requestsStorage -}}
        {{- $isBytes = true -}}
      {{- else if eq $key "pods" -}}
        {{- $req = int64 $r.pods -}}
        {{- /* One surge Pod, for the same reason as the CPU and memory surge. */ -}}
        {{- $surgeRoom = 1 -}}
        {{- $isCount = true -}}
      {{- else if or (eq $key "persistentvolumeclaims") (eq $key "count/persistentvolumeclaims") -}}
        {{- $req = int64 $r.persistentVolumeClaims -}}
        {{- $isCount = true -}}
      {{- end -}}

      {{- if or $isCpu $isBytes $isCount -}}
        {{- $hardVal := 0 -}}
        {{- $usedVal := 0 -}}
        {{- if $isCpu -}}
          {{- $hardVal = include "kube-agents.parseCpuMillis" $hardRaw | int64 -}}
          {{- $usedVal = include "kube-agents.parseCpuMillis" (index $used $key | default "0") | int64 -}}
        {{- else if $isBytes -}}
          {{- $hardVal = include "kube-agents.parseBytes" $hardRaw | int64 -}}
          {{- $usedVal = include "kube-agents.parseBytes" (index $used $key | default "0") | int64 -}}
        {{- else if $isCount -}}
          {{- $hardVal = include "kube-agents.parseCount" $hardRaw | int64 -}}
          {{- $usedVal = include "kube-agents.parseCount" (index $used $key | default "0") | int64 -}}
        {{- end -}}

        {{- $availVal := sub $hardVal $usedVal -}}
        {{- $failed := false -}}
        {{- $reason := "" -}}

        {{- if lt $hardVal $req -}}
          {{- $failed = true -}}
          {{- $reason = "quota hard capacity is less than required" -}}
        {{- else if and $ctx.Release.IsInstall (lt $availVal $req) -}}
          {{- $failed = true -}}
          {{- $reason = "available headroom (hard - used) is less than required for fresh install" -}}
        {{- end -}}

        {{- if $failed -}}
          {{- /* used + required + one surge Pod. Exactly used+required fits the release at
                 rest and then stalls its first rolling update. */ -}}
          {{- $patchTarget := add $usedVal $req $surgeRoom -}}
          {{- $reqFormatted := "" -}}
          {{- $hardFormatted := "" -}}
          {{- $availFormatted := "" -}}
          {{- $patchVal := "" -}}
          {{- if $isCpu -}}
            {{- $reqFormatted = include "kube-agents.formatCpu" $req -}}
            {{- $hardFormatted = include "kube-agents.formatCpu" $hardVal -}}
            {{- $availFormatted = include "kube-agents.formatCpu" $availVal -}}
            {{- $patchVal = include "kube-agents.formatCpu" $patchTarget -}}
          {{- else if $isBytes -}}
            {{- $reqFormatted = include "kube-agents.formatBytesApprox" $req -}}
            {{- /* The quota's own spelling, not a re-rendering of it: `hard 10G` is what
                   `kubectl describe resourcequota` shows, so echoing it verbatim is both
                   shorter and easier to match up than any unit this could pick. */ -}}
            {{- $hardFormatted = toString $hardRaw -}}
            {{- $availFormatted = include "kube-agents.formatBytesApprox" $availVal -}}
            {{- $patchVal = include "kube-agents.formatBytesCeil" $patchTarget -}}
          {{- else -}}
            {{- $reqFormatted = printf "%d" $req -}}
            {{- $hardFormatted = printf "%d" $hardVal -}}
            {{- $availFormatted = printf "%d" $availVal -}}
            {{- $patchVal = printf "%d" $patchTarget -}}
          {{- end -}}

          {{- $line := printf "  - %s: required %s, hard %s, available %s (%s)" $key $reqFormatted $hardFormatted $availFormatted $reason -}}
          {{- $shortfalls = append $shortfalls $line -}}
          {{- $patchEntries = append $patchEntries (printf "%q:%q" $key $patchVal) -}}
        {{- end -}}
      {{- end -}}
    {{- end -}}

    {{- if gt (len $shortfalls) 0 -}}
      {{- $qName := (index (index $quota "metadata" | default dict) "name") | default "resourcequota" -}}
      {{- $patchBody := printf "{\"spec\":{\"hard\":{%s}}}" (join "," $patchEntries) -}}
      {{- $patchCmd := printf "kubectl patch resourcequota %s -n %s --type=strategic --patch '%s'" $qName $ctx.Release.Namespace $patchBody -}}
      {{- $msg := printf "ResourceQuota %q in namespace %q has insufficient capacity for release %q:\n%s\n\nRemediation: increase the quota with:\n  %s\n(those values leave room for one rollout surge Pod)\nor bypass this check with --set quotaPreflight.enabled=false" $qName $ctx.Release.Namespace $ctx.Release.Name (join "\n" $shortfalls) $patchCmd -}}
      {{- fail $msg -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- end }}

{{- define "kube-agents.quotaPreflight" -}}
{{- if .Values.quotaPreflight.enabled -}}
{{- $rawQuotas := lookup "v1" "ResourceQuota" .Release.Namespace "" -}}
{{- $quotas := $rawQuotas | default dict -}}
{{- $items := list -}}
{{- if kindIs "map" $quotas -}}
  {{- $items = index $quotas "items" | default list -}}
{{- end -}}
{{- if gt (len $items) 0 -}}
  {{- $required := include "kube-agents.quotaRequirements" . | fromJson -}}
  {{- include "kube-agents.quotaCheckItems" (dict "ctx" . "items" $items "required" $required) -}}
{{- end -}}
{{- end -}}
{{- end }}
