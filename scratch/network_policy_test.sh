#!/bin/bash
set -euo pipefail

echo "==========================================================="
echo " Network Policy & Dataplane V2 Comprehensive Test Suite"
echo "==========================================================="

NAMESPACE="kubeagents-system"
TEST_NS="default"
API_SERVER_IP=$(kubectl get svc kubernetes -n default -o jsonpath='{.spec.clusterIP}')

echo "Waiting for platform-agent pods to be ready..."
kubectl wait --for=condition=ready pod -l app=platform-agent-gateway -n $NAMESPACE --timeout=60s
AGENT_POD=$(kubectl get pod -l app=platform-agent-gateway -n $NAMESPACE -o jsonpath='{.items[0].metadata.name}')

echo "Waiting for litellm pods to be ready (if deployed)..."
kubectl wait --for=condition=ready pod -l app=litellm -n $NAMESPACE --timeout=5s || true
LITELLM_POD=$(kubectl get pod -l app=litellm -n $NAMESPACE -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

echo "Waiting for github-token-minter pods to be ready (if deployed)..."
kubectl wait --for=condition=ready pod -l app=github-token-minter -n $NAMESPACE --timeout=5s || true
MINTY_POD=$(kubectl get pod -l app=github-token-minter -n $NAMESPACE -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

# Clean up any existing test pods first
kubectl delete pod netpol-test -n $TEST_NS >/dev/null 2>&1 || true
kubectl delete pod internal-netpol-test -n $NAMESPACE >/dev/null 2>&1 || true
kubectl delete pod operator-netpol-test -n $NAMESPACE >/dev/null 2>&1 || true
kubectl delete pod prom-test -n gke-gmp-system >/dev/null 2>&1 || true

# Create a test pod in the default namespace for lateral movement testing
echo "Deploying an external test pod in '$TEST_NS' namespace..."
kubectl run netpol-test --image=alpine --restart=Never -n $TEST_NS --labels="app=netpol-test" -- sleep 3600 >/dev/null 2>&1 || true
kubectl wait --for=condition=ready pod netpol-test -n $TEST_NS --timeout=30s

# Create an internal test pod in kubeagents-system
echo "Deploying an internal test pod in '$NAMESPACE' namespace..."
kubectl run internal-netpol-test --image=alpine --restart=Never -n $NAMESPACE --labels="app=internal-test" -- sleep 3600 >/dev/null 2>&1 || true
kubectl wait --for=condition=ready pod internal-netpol-test -n $NAMESPACE --timeout=30s

# Install curl in test pods
kubectl exec netpol-test -n $TEST_NS -- apk add --no-cache curl >/dev/null
kubectl exec internal-netpol-test -n $NAMESPACE -- apk add --no-cache curl >/dev/null

# Create an operator egress test pod in kubeagents-system to test distroless operator egress
echo "Deploying an operator egress test pod in '$NAMESPACE' namespace..."
kubectl run operator-netpol-test --image=alpine --restart=Never -n $NAMESPACE --labels="control-plane=controller-manager" -- sleep 3600 >/dev/null 2>&1 || true
kubectl wait --for=condition=ready pod operator-netpol-test -n $NAMESPACE --timeout=30s
kubectl exec operator-netpol-test -n $NAMESPACE -- apk add --no-cache curl >/dev/null

echo ""
echo "--- 1. EGRESS TESTS FROM PLATFORM AGENT ---"
echo "1.1 Testing Cluster DNS (Should PASS)"
kubectl exec -n $NAMESPACE $AGENT_POD -- nslookup kubernetes.default.svc || echo "FAILED"

echo "1.1.1 Testing External DNS via Universal DNS Exception (Should PASS due to 0.0.0.0/0 on port 53)"
kubectl exec -n $NAMESPACE $AGENT_POD -- nslookup google.com 8.8.8.8 || echo "FAILED (External DNS blocked)"

echo "1.2 Testing GCP Metadata Server Egress (Should PASS)"
kubectl exec -n $NAMESPACE $AGENT_POD -- curl -s -m 5 -H "Metadata-Flavor: Google" http://169.254.169.254/computeMetadata/v1/instance/id || echo "FAILED"

echo "1.2.1 Testing GCP Workload Identity Daemon Egress on port 988 (Optional / Advisory)"
kubectl exec -n $NAMESPACE $AGENT_POD -- curl -s -m 5 http://169.254.169.254:988 || echo "PASS (Port 988 closed or optional on standard GKE node configurations)"

echo "1.3 Testing External HTTPS Allowed Egress (Should PASS)"
kubectl exec -n $NAMESPACE $AGENT_POD -- curl -s -m 5 -I https://api.github.com | head -n 1 || echo "FAILED"

echo "1.4 Testing External HTTP Egress (Should FAIL - blocked by L4 policy except Metadata)"
kubectl exec -n $NAMESPACE $AGENT_POD -- curl -s -m 3 -I http://example.com | head -n 1 || echo "PASS (Blocked as expected)"

echo "1.5 Testing Lateral Movement - Accessing default namespace (Should FAIL)"
# Get the IP of the test pod in default ns
TEST_POD_IP=$(kubectl get pod netpol-test -n $TEST_NS -o jsonpath='{.status.podIP}')
# We spin up a dummy server in the test pod on port 443 to test if the 0.0.0.0/0 port 443 except block works
kubectl exec netpol-test -n $TEST_NS -- /bin/sh -c "nc -l -p 443 </dev/null >/dev/null 2>&1 &" >/dev/null 2>&1 &
sleep 1
kubectl exec -n $NAMESPACE $AGENT_POD -- curl -s -m 3 https://$TEST_POD_IP:443 || echo "PASS (Blocked as expected)"

echo "1.6 Testing Lateral Movement - Accessing CGNAT (100.64.x.x) block (Should FAIL)"
# Attempting to access an arbitrary internal IP in the 100.64.0.0/10 range
kubectl exec -n $NAMESPACE $AGENT_POD -- curl -s -m 3 https://100.64.1.1 || echo "PASS (Blocked as expected)"

echo "1.7 Testing Internal Kubernetes API Server Egress on Port 443 (Should PASS)"
kubectl exec -n $NAMESPACE $AGENT_POD -- curl -s -m 5 -k https://$API_SERVER_IP:443/api || echo "FAILED"

echo "1.7.1 Testing Internal Kubernetes API Server Egress on Port 6443 (Should PASS)"
kubectl exec -n $NAMESPACE $AGENT_POD -- curl -s -m 5 -k https://$API_SERVER_IP:6443/api || echo "FAILED"

if kubectl get svc opentelemetry-collector -n gke-managed-otel >/dev/null 2>&1; then
    echo "1.8 Testing GKE Managed OpenTelemetry Collector Egress on Port 4318 (Should PASS)"
    kubectl exec -n $NAMESPACE $AGENT_POD -- curl -s -m 5 http://opentelemetry-collector.gke-managed-otel.svc:4318/v1/traces || echo "FAILED"

    echo "1.8.1 Testing GKE Managed OpenTelemetry Collector Egress on Port 4317 (gRPC) (Should PASS)"
    kubectl exec -n $NAMESPACE $AGENT_POD -- curl -s -m 5 http://opentelemetry-collector.gke-managed-otel.svc:4317 > /dev/null || echo "FAILED (Connection refused or timeout)"
else
    echo "1.8 / 1.8.1 GKE Managed OpenTelemetry Collector not deployed, skipping."
fi

echo ""
echo "--- 2. INGRESS TESTS TO PLATFORM AGENT ---"
AGENT_IP=$(kubectl get pod $AGENT_POD -n $NAMESPACE -o jsonpath='{.status.podIP}')

echo "2.1 Access from within $NAMESPACE to Agent API Port 8642 (Should PASS)"
kubectl exec internal-netpol-test -n $NAMESPACE -- curl -s -m 3 http://$AGENT_IP:8642 || echo "FAILED (or returned non-200)"

echo "2.1.1 Access from within $NAMESPACE to Agent Metrics Port 8643 (Should PASS)"
kubectl exec internal-netpol-test -n $NAMESPACE -- curl -s -m 3 http://$AGENT_IP:8643 || echo "FAILED (or returned non-200)"

echo "2.2 Access from external namespace ($TEST_NS) to Agent API Port 8642 (Should FAIL)"
kubectl exec netpol-test -n $TEST_NS -- curl -s -m 3 http://$AGENT_IP:8642 || echo "PASS (Blocked as expected)"

echo "2.3 Access from external namespace ($TEST_NS) to Agent Metrics Port 8643 (Should FAIL)"
kubectl exec netpol-test -n $TEST_NS -- curl -s -m 3 http://$AGENT_IP:8643 || echo "PASS (Blocked as expected)"

echo "2.4 Access from gke-gmp-system namespace to Agent Metrics Port 8643 (Should PASS)"
# Deploy prometheus dummy pod
kubectl run prom-test --image=alpine --restart=Never -n gke-gmp-system --labels="app=prom-test" -- sleep 3600 >/dev/null 2>&1 || true
kubectl wait --for=condition=ready pod prom-test -n gke-gmp-system --timeout=30s
kubectl exec prom-test -n gke-gmp-system -- apk add --no-cache curl >/dev/null
kubectl exec prom-test -n gke-gmp-system -- curl -s -m 3 http://$AGENT_IP:8643 || echo "FAILED (or returned non-200)"

echo "2.5 Access from within $NAMESPACE to Dashboard Port 9119 (Should PASS)"
kubectl exec internal-netpol-test -n $NAMESPACE -- curl -s -m 3 http://$AGENT_IP:9119 || echo "FAILED (or returned non-200)"

echo "2.6 Access from external namespace ($TEST_NS) to Dashboard Port 9119 (Should FAIL)"
kubectl exec netpol-test -n $TEST_NS -- curl -s -m 3 http://$AGENT_IP:9119 || echo "PASS (Blocked as expected)"


echo ""
echo "--- 3. LITELLM GATEWAY TESTS (If deployed) ---"
if [ -n "$LITELLM_POD" ]; then
    LITELLM_IP=$(kubectl get pod $LITELLM_POD -n $NAMESPACE -o jsonpath='{.status.podIP}')
    echo "3.1 Egress to External HTTPS from LiteLLM (Should PASS)"
    kubectl exec -n $NAMESPACE $LITELLM_POD -- curl -s -m 5 -I https://api.openai.com | head -n 1 || echo "FAILED"

    echo "3.1.1 Egress to GCP Metadata Server from LiteLLM (Should PASS)"
    kubectl exec -n $NAMESPACE $LITELLM_POD -- curl -s -m 5 -H "Metadata-Flavor: Google" http://169.254.169.254/computeMetadata/v1/instance/id || echo "FAILED"

    echo "3.1.2 Egress to GKE Managed OpenTelemetry Collector (HTTP) from LiteLLM (Should PASS)"
    kubectl exec -n $NAMESPACE $LITELLM_POD -- curl -s -m 5 http://opentelemetry-collector.gke-managed-otel.svc:4318/v1/traces || echo "FAILED"

    echo "3.1.3 Egress to GKE Managed OpenTelemetry Collector (gRPC) from LiteLLM (Should PASS)"
    kubectl exec -n $NAMESPACE $LITELLM_POD -- curl -s -m 5 http://opentelemetry-collector.gke-managed-otel.svc:4317 > /dev/null || echo "FAILED (Connection refused or timeout)"

    echo "3.1.4 Egress to Cluster DNS from LiteLLM (Should PASS)"
    kubectl exec -n $NAMESPACE $LITELLM_POD -- nslookup kubernetes.default.svc || echo "FAILED"

    echo "3.2 Egress to Internal Network from LiteLLM (Should FAIL)"
    kubectl exec -n $NAMESPACE $LITELLM_POD -- curl -s -k -m 3 https://$TEST_POD_IP:443 || echo "PASS (Blocked as expected)"

    echo "3.2.1 Egress to CGNAT block from LiteLLM (Should FAIL)"
    kubectl exec -n $NAMESPACE $LITELLM_POD -- curl -s -m 3 https://100.64.1.1 || echo "PASS (Blocked as expected)"

    echo "3.2.2 Egress to External HTTP from LiteLLM (Should FAIL)"
    kubectl exec -n $NAMESPACE $LITELLM_POD -- curl -s -m 3 -I http://example.com | head -n 1 || echo "PASS (Blocked as expected)"

    echo "3.3 Ingress to LiteLLM (Port 4000) from Platform Agent (Should PASS)"
    kubectl exec -n $NAMESPACE $AGENT_POD -- curl -s -m 3 http://$LITELLM_IP:4000/health || echo "FAILED (or non-200)"

    echo "3.4 Ingress to LiteLLM from external namespace (Should FAIL)"
    kubectl exec netpol-test -n $TEST_NS -- curl -s -m 3 http://$LITELLM_IP:4000/health || echo "PASS (Blocked as expected)"

    echo "3.5 Ingress to LiteLLM from gke-gmp-system namespace (Should PASS)"
    kubectl exec prom-test -n gke-gmp-system -- curl -s -m 3 http://$LITELLM_IP:4000/health || echo "FAILED (or returned non-200)"
else
    echo "LiteLLM not deployed, skipping tests."
fi

echo ""
echo "--- 3B. vLLM GEMMA GATEWAY TESTS (If deployed) ---"
echo "Waiting for gemma-server pods to be ready (if deployed)..."
kubectl wait --for=condition=ready pod -l app=gemma-server -n $NAMESPACE --timeout=5s || true
VLLM_POD=$(kubectl get pod -l app=gemma-server -n $NAMESPACE -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -n "$VLLM_POD" ]; then
    VLLM_IP=$(kubectl get pod $VLLM_POD -n $NAMESPACE -o jsonpath='{.status.podIP}')
    echo "3B.1 Egress to External HTTPS from vLLM (Should PASS)"
    kubectl exec -n $NAMESPACE $VLLM_POD -- curl -s -m 5 -I https://huggingface.co | head -n 1 || echo "FAILED"

    echo "3B.1.1 Egress to GCP Metadata Server from vLLM (Should PASS)"
    kubectl exec -n $NAMESPACE $VLLM_POD -- curl -s -m 5 -H "Metadata-Flavor: Google" http://169.254.169.254/computeMetadata/v1/instance/id || echo "FAILED"

    echo "3B.1.2 Egress to GKE Managed OpenTelemetry Collector (HTTP) from vLLM (Should PASS)"
    kubectl exec -n $NAMESPACE $VLLM_POD -- curl -s -m 5 http://opentelemetry-collector.gke-managed-otel.svc:4318/v1/traces || echo "FAILED"

    echo "3B.1.3 Egress to GKE Managed OpenTelemetry Collector (gRPC) from vLLM (Should PASS)"
    kubectl exec -n $NAMESPACE $VLLM_POD -- curl -s -m 5 http://opentelemetry-collector.gke-managed-otel.svc:4317 > /dev/null || echo "FAILED (Connection refused or timeout)"

    echo "3B.1.4 Egress to Cluster DNS from vLLM (Should PASS)"
    kubectl exec -n $NAMESPACE $VLLM_POD -- nslookup kubernetes.default.svc || echo "FAILED"

    echo "3B.2 Egress to Internal Network from vLLM (Should FAIL)"
    kubectl exec -n $NAMESPACE $VLLM_POD -- curl -s -k -m 3 https://$TEST_POD_IP:443 || echo "PASS (Blocked as expected)"

    echo "3B.3 Egress to CGNAT block from vLLM (Should FAIL)"
    kubectl exec -n $NAMESPACE $VLLM_POD -- curl -s -m 3 https://100.64.1.1 || echo "PASS (Blocked as expected)"

    echo "3B.3.1 Egress to External HTTP from vLLM (Should FAIL)"
    kubectl exec -n $NAMESPACE $VLLM_POD -- curl -s -m 3 -I http://example.com | head -n 1 || echo "PASS (Blocked as expected)"

    echo "3B.4 Ingress to vLLM from within $NAMESPACE (Should PASS)"
    kubectl exec internal-netpol-test -n $NAMESPACE -- curl -s -m 3 http://$VLLM_IP:8000/health || echo "FAILED (or non-200)"

    echo "3B.4.1 Ingress to vLLM from Platform Agent (Should PASS)"
    kubectl exec -n $NAMESPACE $AGENT_POD -- curl -s -m 3 http://$VLLM_IP:8000/health || echo "FAILED (or non-200)"

    echo "3B.5 Ingress to vLLM from external namespace (Should FAIL)"
    kubectl exec netpol-test -n $TEST_NS -- curl -s -m 3 http://$VLLM_IP:8000/health || echo "PASS (Blocked as expected)"

    echo "3B.6 Ingress to vLLM from gke-gmp-system namespace (Should PASS)"
    kubectl exec prom-test -n gke-gmp-system -- curl -s -m 3 http://$VLLM_IP:8000/health || echo "FAILED (or returned non-200)"
else
    echo "vLLM Gemma not deployed, skipping tests."
fi


echo ""
echo "--- 4. FQDN NETWORK POLICY / DATAPLANE V2 TESTS ---"
echo "Note: If Dataplane V2 is enabled, FQDN Network Policies restrict outbound traffic further."
echo "4.1 Egress to Allowed FQDN (github.com) (Should PASS)"
kubectl exec -n $NAMESPACE $AGENT_POD -- curl -s -m 5 -I https://github.com | head -n 1 || echo "FAILED"

echo "4.1.1 Egress to Allowed FQDN (googleapis.com) for MCP (Should PASS)"
kubectl exec -n $NAMESPACE $AGENT_POD -- curl -s -m 5 -I https://container.googleapis.com/v1/projects | head -n 1 || echo "FAILED"

echo "4.2 Egress to Disallowed FQDN (yahoo.com) (Should FAIL via Dataplane V2, even if L4 allows 443)"
kubectl exec -n $NAMESPACE $AGENT_POD -- curl -s -m 5 -I https://yahoo.com || echo "PASS (Blocked by FQDN policy as expected)"


echo "--- 5. GITHUB TOKEN MINTER TESTS (If deployed) ---"
if [ -n "$MINTY_POD" ]; then
    MINTY_IP=$(kubectl get pod $MINTY_POD -n $NAMESPACE -o jsonpath='{.status.podIP}')
    echo "5.1 Egress to External HTTPS from Minty (Should PASS)"
    kubectl exec -n $NAMESPACE $MINTY_POD -- curl -s -m 5 -I https://api.github.com | head -n 1 || echo "FAILED"

    echo "5.1.0 Egress to Cluster DNS from Minty (Should PASS)"
    kubectl exec -n $NAMESPACE $MINTY_POD -- nslookup kubernetes.default.svc || echo "FAILED"

    echo "5.1.1 Egress to Internal Network from Minty (Should FAIL)"
    kubectl exec -n $NAMESPACE $MINTY_POD -- curl -s -k -m 3 https://$TEST_POD_IP:443 || echo "PASS (Blocked as expected)"

    echo "5.1.2 Egress to CGNAT block from Minty (Should FAIL)"
    kubectl exec -n $NAMESPACE $MINTY_POD -- curl -s -m 3 https://100.64.1.1 || echo "PASS (Blocked as expected)"

    echo "5.1.3 Egress to External HTTP from Minty (Should FAIL)"
    kubectl exec -n $NAMESPACE $MINTY_POD -- curl -s -m 3 -I http://example.com | head -n 1 || echo "PASS (Blocked as expected)"

    echo "5.2 Ingress to Minty from Platform Agent (Should PASS)"
    # Note: Platform Agent has kubeagents.x-k8s.io/has-credential-proxy: "true"
    kubectl exec -n $NAMESPACE $AGENT_POD -- curl -s -m 3 http://$MINTY_IP:8080/version || echo "FAILED (or non-200)"

    echo "5.3 Ingress to Minty from internal dummy pod without label (Should FAIL)"
    kubectl exec internal-netpol-test -n $NAMESPACE -- curl -s -m 3 http://$MINTY_IP:8080/version || echo "PASS (Blocked as expected)"
else
    echo "GitHub Token Minter not deployed, skipping tests."
fi

echo ""
echo "--- 6. OPERATOR NETWORK ISOLATION TESTS ---"
echo "Note: The kube-agents-operator controller-manager now has a NetworkPolicy."
echo "6.1 Ingress to Operator Webhook (9443) from external namespace (Should PASS due to NetworkPolicy allowing API Server)"
OPERATOR_IP=$(kubectl get pod -l app.kubernetes.io/name=kube-agents-operator -n kubeagents-system -o jsonpath='{.items[0].status.podIP}' 2>/dev/null || true)
if [ -n "$OPERATOR_IP" ]; then
    kubectl exec netpol-test -n $TEST_NS -- curl -s -k -m 3 https://$OPERATOR_IP:9443 > /dev/null || echo "FAILED"

    echo "6.1.1 Ingress to Operator Webhook (9443) from internal namespace (Should PASS)"
    kubectl exec internal-netpol-test -n $NAMESPACE -- curl -s -k -m 3 https://$OPERATOR_IP:9443 || echo "FAILED"

    echo "6.1.2 Ingress to Operator Metrics (8081) from internal namespace (Should PASS)"
    kubectl exec internal-netpol-test -n $NAMESPACE -- curl -s -m 3 http://$OPERATOR_IP:8081/metrics || echo "FAILED"
else
    echo "Operator not found, skipping."
fi

echo "6.2 Egress to Kubernetes API Server from Operator (Should PASS)"
kubectl exec operator-netpol-test -n $NAMESPACE -- curl -s -m 5 -k https://$API_SERVER_IP:443/api || echo "FAILED"

echo "6.2.1 Egress to Cluster DNS from Operator (Should PASS)"
kubectl exec operator-netpol-test -n $NAMESPACE -- nslookup kubernetes.default.svc || echo "FAILED"

echo "6.3 Egress to External HTTPS from Operator (Should PASS)"
kubectl exec operator-netpol-test -n $NAMESPACE -- curl -s -m 5 -I https://google.com | head -n 1 || echo "FAILED"

echo "6.4 Egress to Internal Non-API Network from Operator (Lateral Movement) (Should FAIL)"
# Operator should not be able to talk to arbitrary internal IPs on 443 (other than $API_SERVER_IP)
kubectl exec operator-netpol-test -n $NAMESPACE -- curl -s -k -m 3 https://$TEST_POD_IP:443 || echo "PASS (Blocked as expected)"

echo "6.5 Egress to External HTTP from Operator (Should FAIL)"
kubectl exec operator-netpol-test -n $NAMESPACE -- curl -s -m 3 -I http://example.com | head -n 1 || echo "PASS (Blocked as expected)"

echo "==========================================================="
echo " Cleaning up..."
kubectl delete pod netpol-test -n $TEST_NS >/dev/null 2>&1
kubectl delete pod internal-netpol-test -n $NAMESPACE >/dev/null 2>&1
kubectl delete pod operator-netpol-test -n $NAMESPACE >/dev/null 2>&1
echo " Done."
