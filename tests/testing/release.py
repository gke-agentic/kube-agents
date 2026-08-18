"""Release pipeline specific test constants and fixtures."""

MOCK_REQUIRED_RELEASE_IMAGES = [
    "k8s-operator",
    "platform-agent",
    "credential-proxy",
    "replay-proxy",
]

MOCK_INITIAL_VERSION = "0.1.0"
MOCK_BASE_TAG_PRE_1_0 = "0.1.4"
MOCK_BASE_TAG_1_X = "1.2.3"
MOCK_RC_VALIDATED_TAG = "rc_0.2.0_validated"
MOCK_TARGET_RELEASE_TAG = "0.2.0"
MOCK_COLLIDING_RELEASE_TAG = "0.1.9"

MOCK_EMERGENCY_OVERRIDE_REASON = "INCIDENT_NUMBER critical security hotfix"

MOCK_COMMIT_MSG_FEAT = "feat(agent): add multi-cluster discovery"
MOCK_COMMIT_MSG_FIX = "fix(installer): resolve port conflict"
MOCK_COMMIT_MSG_DOCS = "docs: update installation instructions"
MOCK_COMMIT_MSG_BREAKING_PRE_1_0 = "feat(operator)!: break CRD schema format"
MOCK_COMMIT_MSG_BREAKING_1_X = "feat!: remove deprecated v1alpha1 APIs"
MOCK_COMMIT_MSG_BREAKING_BODY = "refactor: overhaul config format\n\nBREAKING CHANGE: old yaml spec is deprecated"

# Supported pure numeric SemVer release tags (X.Y.Z)
VALID_GA_RELEASE_TAGS = [
    "0.1.0",
    "0.2.0",
    "1.0.0",
    "1.2.3",
]

# Unsupported GA release tags (v-prefixed, pre-releases, branches, malformed strings)
INVALID_GA_RELEASE_TAGS = [
    "v0.1.0",
    "v0.2.0",
    "0.1",
    "main",
    "latest",
    "0.1.0-alpha",
    "0.1.0-rc1",
    "0.2.3-rc.1",
    "release",
]

