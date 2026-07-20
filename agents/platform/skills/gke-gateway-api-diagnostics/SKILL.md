---
name: gke-gateway-api-diagnostics
description: Diagnoses GKE Gateway API routes, HTTPRoute statuses, TLS cert expirations, and Cloud Armor policies.
---

# GKE Gateway API Diagnostics Skill

This skill provides diagnostic workflows for inspecting GKE Gateway API resources, including Gateway controllers, HTTPRoute mappings, ManagedCertificate provisioning, and Cloud Armor backends.

## Workflows

### 1. Audit GKE Gateways

List all configured Gateways and retrieve status conditions to verify that the GKE Gateway controller has provisioned the load balancers successfully.

**Command:**
```bash
kubectl get gateways --all-namespaces
kubectl describe gateway <gateway-name> -n <namespace>
```

### 2. Diagnose HTTPRoutes

Verify that HTTPRoute resources are correctly attached to their respective Gateways and that parentRefs are resolved.

**Command:**
```bash
kubectl get httproutes --all-namespaces
kubectl describe httproute <route-name> -n <namespace>
```

### 3. Check Managed Certificates

For external GKE Gateways, inspect Google-managed SSL/TLS certificates to verify domain propagation and provisioning status.

**Command:**
```bash
kubectl get managedcertificates --all-namespaces
kubectl describe managedcertificate <cert-name> -n <namespace>
```
