# GKE Staging Workloads: Development Team Onboarding & Guidelines

Welcome to the Multi-Cluster GKE Staging and Testing Environment. This repository contains the configurations and templates required to deploy, test, and validate our agentic GKE cluster operations.

## Operational Readiness & Best Practices

To ensure stability, efficiency, and resource optimization across our staging infrastructure, all team members are requested to observe the following guidelines:

*   **Configuration Validation:** Verify your parameters within `variables.tf` and related map definitions prior to executing `deploy_infra.sh`.
*   **Scoped Verification:** Conduct initial dry-runs or target smaller cluster subsets when validating non-trivial or experimental logic.
*   **Resource Lifecycle Management:** Always execute `teardown_infra.sh` upon completing testing cycles to release cluster allocations and optimize operational costs.

Thank you for your dedication to maintaining a robust and stable staging ecosystem. For questions, please reach out via our standard engineering channels.
