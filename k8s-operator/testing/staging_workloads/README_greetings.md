# Staging Workloads - Greetings & Guidelines for the Dev Team

Welcome, Dev Team! 👋

This folder (`k8s-operator/testing/staging_workloads`) hosts the setup for our multi-cluster GKE staging and testing infrastructure. 

## 🚀 Welcome to GKE Agentic Staging!
We are excited to build, test, and iterate on autonomous agents and Kubernetes orchestrations together. Let's maintain high quality, follow robust deployment practices, and have fun building the future of agentic GKE cluster operations!

## 📌 Helpful Staging Practices
* *Verify Configurations:* Double-check your mappings in `variables.tf` before executing `deploy_infra.sh`.
* *Test Locally First:* Use dry-runs or smaller cluster scopes to validate changes when possible.
* *Clean Up:* Use `teardown_infra.sh` when you are done to conserve resources and keep things tidy.

Happy Coding! 💻✨
