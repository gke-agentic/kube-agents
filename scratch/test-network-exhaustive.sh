#!/usr/bin/env bash
set -e

# ANSI Colors
C_GREEN="\e[32m"
C_RED="\e[31m"
C_CYAN="\e[36m"
C_YELLOW="\e[33m"
C_RESET="\e[0m"

echo -e "${C_CYAN}======================================================${C_RESET}"
echo -e "${C_CYAN}  Kube-Agents Exhaustive Network Policy Test Matrix  ${C_RESET}"
echo -e "${C_CYAN}======================================================${C_RESET}\n"

# 1. Setup Test Environment
echo -e "${C_YELLOW}Setting up test pods...${C_RESET}"
kubectl create namespace gke-gmp-system --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace gke-gmp-system kubernetes.io/metadata.name=gke-gmp-system --overwrite

# Create long-running test pods (avoiding AlreadyExists errors from kubectl run --rm)
kubectl run test-client-system --image=curlimages/curl --namespace=kubeagents-system --labels="app=test-client" --restart=Never -- sleep 3600 &>/dev/null || true
kubectl run test-client-gmp --image=curlimages/curl --namespace=gke-gmp-system --labels="app=test-client" --restart=Never -- sleep 3600 &>/dev/null || true
kubectl run test-client-default --image=curlimages/curl --namespace=default --labels="app=test-client" --restart=Never -- sleep 3600 &>/dev/null || true

# Wait for pods to be ready
kubectl wait --for=condition=Ready pod/test-client-system -n kubeagents-system --timeout=30s
kubectl wait --for=condition=Ready pod/test-client-gmp -n gke-gmp-system --timeout=30s
kubectl wait --for=condition=Ready pod/test-client-default -n default --timeout=30s

AGENT_POD=$(kubectl get pod -n kubeagents-system -l app=platform-agent-gateway -o jsonpath='{.items[0].metadata.name}')
AGENT_IP=$(kubectl get pod -n kubeagents-system -l app=platform-agent-gateway -o jsonpath='{.items[0].status.podIP}')
LITELLM_IP=$(kubectl get pod -n kubeagents-system -l app=litellm -o jsonpath='{.items[0].status.podIP}' 2>/dev/null || echo "127.0.0.1")

run_test() {
  local description="$1"
  local cmd="$2"
  local expect_success="$3"
  
  echo -n -e "Testing ${description}... "
  if eval "$cmd" &>/dev/null; then
    if [ "$expect_success" = true ]; then
      echo -e "${C_GREEN}[PASS] Allowed${C_RESET}"
    else
      echo -e "${C_RED}[FAIL] Allowed (Should be blocked)${C_RESET}"
    fi
  else
    if [ "$expect_success" = false ]; then
      echo -e "${C_GREEN}[PASS] Blocked${C_RESET}"
    else
      echo -e "${C_RED}[FAIL] Blocked (Should be allowed)${C_RESET}"
    fi
  fi
}

echo -e "\n${C_CYAN}--- A. Platform Agent Egress Tests (From Agent Pod) ---${C_RESET}"
# Allowed Egress
run_test "E-01: DNS Resolution (UDP 53)" "kubectl exec -n kubeagents-system $AGENT_POD -c platform-agent -- nslookup kubernetes.default.svc" true
run_test "E-02: Metadata Server (TCP 80)" "kubectl exec -n kubeagents-system $AGENT_POD -c platform-agent -- curl -s -m 3 http://169.254.169.254/computeMetadata/v1/" true
run_test "E-03: K8s API Server (TCP 443)" "kubectl exec -n kubeagents-system $AGENT_POD -c platform-agent -- curl -s -k -m 3 https://kubernetes.default.svc/api" true
run_test "E-04: LiteLLM Gateway (TCP 80)" "kubectl exec -n kubeagents-system $AGENT_POD -c platform-agent -- curl -s -m 3 http://litellm.kubeagents-system.svc:80/health" true
run_test "E-05: OTel Collector (TCP 4318)" "kubectl exec -n kubeagents-system $AGENT_POD -c platform-agent -- curl -s -m 3 http://opentelemetry-collector.gke-managed-otel.svc:4318/v1/traces" true
run_test "E-06: External HTTPS (TCP 443)" "kubectl exec -n kubeagents-system $AGENT_POD -c platform-agent -- curl -s -m 3 https://www.googleapis.com" true
# E-07 GitHub Minter might not be running, so we test connectivity to its IP/Service if it exists. We'll skip it in automated pass/fail if it doesn't exist, but here we expect timeout if not running, so we just run a mock test.
# run_test "E-07: GitHub Token Minter (TCP 8080)" "kubectl exec -n kubeagents-system $AGENT_POD -c platform-agent -- curl -s -m 3 http://github-token-minter.kubeagents-system.svc:8080/token" true

# Blocked Egress
run_test "E-08: External HTTP (TCP 80)" "kubectl exec -n kubeagents-system $AGENT_POD -c platform-agent -- curl -s -m 3 http://www.googleapis.com" false
run_test "E-09: Lateral Movement RFC 1918 (10.x.x.x)" "kubectl exec -n kubeagents-system $AGENT_POD -c platform-agent -- curl -s -m 3 https://10.1.1.1" false
run_test "E-10: Lateral Movement RFC 1918 (172.x.x.x)" "kubectl exec -n kubeagents-system $AGENT_POD -c platform-agent -- curl -s -m 3 https://172.16.1.1" false
run_test "E-11: Lateral Movement RFC 1918 (192.x.x.x)" "kubectl exec -n kubeagents-system $AGENT_POD -c platform-agent -- curl -s -m 3 https://192.168.1.1" false
run_test "E-12: Lateral Movement CGNAT (100.64.x.x)" "kubectl exec -n kubeagents-system $AGENT_POD -c platform-agent -- curl -s -m 3 https://100.64.1.1" false


echo -e "\n${C_CYAN}--- B. Platform Agent Ingress Tests (To Agent Pod) ---${C_RESET}"
# Allowed Ingress
run_test "I-01: From Same Namespace (Port 8642)" "kubectl exec -n kubeagents-system test-client-system -- curl -s -m 3 http://platform-agent.kubeagents-system.svc.cluster.local:8642/healthz" true
run_test "I-02: From Same Namespace (Port 9119)" "kubectl exec -n kubeagents-system test-client-system -- curl -s -m 3 http://platform-agent.kubeagents-system.svc.cluster.local:9119" true
run_test "I-03: From GMP Namespace (Port 8643)" "kubectl exec -n gke-gmp-system test-client-gmp -- curl -s -m 3 http://$AGENT_IP:8643/health" true

# Blocked Ingress
run_test "I-04: From Default Namespace (Port 8642)" "kubectl exec -n default test-client-default -- curl -s -m 3 http://platform-agent.kubeagents-system.svc.cluster.local:8642/healthz" false
run_test "I-05: From Default Namespace (Port 9119)" "kubectl exec -n default test-client-default -- curl -s -m 3 http://platform-agent.kubeagents-system.svc.cluster.local:9119" false
run_test "I-06: From GMP Namespace (Port 8642 - Wrong Port)" "kubectl exec -n gke-gmp-system test-client-gmp -- curl -s -m 3 http://platform-agent.kubeagents-system.svc.cluster.local:8642/healthz" false


echo -e "\n${C_CYAN}--- C. LiteLLM Egress Tests (From LiteLLM Pod) ---${C_RESET}"
if [ "$LITELLM_IP" != "127.0.0.1" ]; then
  LITELLM_POD=$(kubectl get pod -n kubeagents-system -l app=litellm -o jsonpath='{.items[0].metadata.name}')
  
  # Allowed Egress
  run_test "LE-01: External HTTPS (TCP 443)" "kubectl exec -n kubeagents-system $LITELLM_POD -- python3 -c \"import urllib.request; urllib.request.urlopen('https://www.googleapis.com', timeout=3)\"" true
  
  # Blocked Egress
  run_test "LE-02: Lateral Movement RFC 1918 (10.x.x.x)" "kubectl exec -n kubeagents-system $LITELLM_POD -- python3 -c \"import urllib.request; urllib.request.urlopen('https://10.1.1.1', timeout=3)\"" false
  # Note: 100.64.x.x is NOT blocked in LiteLLM policy currently. We will test it expecting it to be blocked to reveal the bug.
  run_test "LE-03: Lateral Movement CGNAT (100.64.x.x)" "kubectl exec -n kubeagents-system $LITELLM_POD -- python3 -c \"import urllib.request; urllib.request.urlopen('https://100.64.1.1', timeout=3)\"" false
else
  echo -e "${C_YELLOW}Skipping LiteLLM Egress Tests (LiteLLM pod not found)${C_RESET}"
fi


echo -e "\n${C_CYAN}--- D. LiteLLM Ingress Tests (To LiteLLM Pod) ---${C_RESET}"
if [ "$LITELLM_IP" != "127.0.0.1" ]; then
  # Allowed Ingress
  run_test "LI-01: From Same Namespace (Port 4000)" "kubectl exec -n kubeagents-system test-client-system -- curl -s -m 3 http://$LITELLM_IP:4000" true
  run_test "LI-02: From GMP Namespace (Port 4000)" "kubectl exec -n gke-gmp-system test-client-gmp -- curl -s -m 3 http://$LITELLM_IP:4000" true
  
  # Blocked Ingress
  run_test "LI-03: From Default Namespace (Port 4000)" "kubectl exec -n default test-client-default -- curl -s -m 3 http://$LITELLM_IP:4000" false
else
  echo -e "${C_YELLOW}Skipping LiteLLM Ingress Tests (LiteLLM pod not found)${C_RESET}"
fi

# Cleanup
echo -e "\n${C_YELLOW}Cleaning up test pods...${C_RESET}"
kubectl delete pod test-client-system -n kubeagents-system &>/dev/null || true
kubectl delete pod test-client-gmp -n gke-gmp-system &>/dev/null || true
kubectl delete pod test-client-default -n default &>/dev/null || true

echo -e "\n${C_GREEN}Test matrix execution complete!${C_RESET}"
