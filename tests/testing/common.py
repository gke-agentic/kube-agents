"""Common test constants and fixtures shared across test suites."""

MOCK_DEFAULT_RELEASE_REPO = "gke-labs/kube-agents"
MOCK_DEFAULT_REGISTRY_PREFIX = "ghcr.io/gke-labs/kube-agents"
MOCK_CUSTOM_ORG = "custom-org"
MOCK_CUSTOM_REPO = "custom-repo"
MOCK_CUSTOM_TARGET_REPO = "custom-org/custom-repo"
MOCK_CUSTOM_REGISTRY_PREFIX = "us-docker.pkg.dev/my-proj/my-repo"

TRUTHY_BOOLEAN_INPUTS = [
    "true",
    "True",
    "TRUE",
    "yes",
    "YES",
    "y",
    "1",
    "on",
    "  true  ",
]

FALSY_BOOLEAN_INPUTS = [
    "false",
    "0",
    "no",
    "off",
    "",
    "random",
    "null",
]


def get_isolated_test_env(overrides=None, bin_dir=None):
    """Returns a sanitized environment for hermetic script execution, free of CI runner pollution."""
    import os

    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("GITHUB_", "RUNNER_")) and k not in ("CI", "CONTINUOUS_INTEGRATION")
    }
    if bin_dir:
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    if overrides:
        env.update(overrides)
    return env
