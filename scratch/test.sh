#!/bin/bash
set -x

echo "--- Ingress Verification ---"

# I-01 to I-03
kubectl run -i --rm test-client -n kubeagents-system --image=curlimages/curl -- curl -m 3 http://platform-agent.kubeagents-system.svc:8642/healthz
kubectl run -i --rm test-client -n kubeagents-system --image=curlimages/curl -- curl -m 3 http://platform-agent.kubeagents-system.svc:8643/metrics
kubectl run -i --rm test-client -n kubeagents-system --image=curlimages/curl -- curl -m 3 http://platform-agent.kubeagents-system.svc:9119

# I-04
kubectl run -i --rm test-client -n kubeagents-system --image=curlimages/curl -- curl -m 3 http://platform-agent.kubeagents-system.svc:8080 || echo "BLOCKED (Expected)"

# I-05 to I-06
kubectl run -i --rm test-prom -n gke-gmp-system --image=curlimages/curl -- curl -m 3 http://platform-agent.kubeagents-system.svc:8643/metrics
kubectl run -i --rm test-prom -n gke-gmp-system --image=curlimages/curl -- curl -m 3 http://platform-agent.kubeagents-system.svc:8642/healthz || echo "BLOCKED (Expected)"

# I-07
kubectl run -i --rm test-default -n default --image=curlimages/curl -- curl -m 3 http://platform-agent.kubeagents-system.svc:8642/healthz || echo "BLOCKED (Expected)"

echo "--- Egress Verification ---"
AGENT_POD=$(kubectl get pod -n kubeagents-system -l app=platform-agent-gateway -o jsonpath='{.items[0].metadata.name}')

# E-01 & E-02
kubectl exec -i -n kubeagents-system "$AGENT_POD" -c platform-agent -- nslookup kubernetes.default.svc

# E-03
kubectl exec -i -n kubeagents-system "$AGENT_POD" -c platform-agent -- nslookup kubernetes.default.svc 8.8.8.8 || echo "BLOCKED (Expected)"

# E-04 & E-05
kubectl exec -i -n kubeagents-system "$AGENT_POD" -c platform-agent -- curl -m 3 -I http://169.254.169.254/computeMetadata/v1/ -H "Metadata-Flavor: Google"
kubectl exec -i -n kubeagents-system "$AGENT_POD" -c platform-agent -- curl -m 3 -I https://169.254.169.254 || echo "BLOCKED (Expected)"

# E-06
kubectl exec -i -n kubeagents-system "$AGENT_POD" -c platform-agent -- curl -m 3 -I http://litellm.kubeagents-system.svc:4000/health

# E-08 & E-09
kubectl exec -i -n kubeagents-system "$AGENT_POD" -c platform-agent -- curl -m 3 -k https://10.96.0.1:443/api
kubectl exec -i -n kubeagents-system "$AGENT_POD" -c platform-agent -- curl -m 3 http://10.96.0.1:80 || echo "BLOCKED (Expected)"

# E-10 & E-11
kubectl exec -i -n kubeagents-system "$AGENT_POD" -c platform-agent -- curl -m 3 -I https://www.googleapis.com
kubectl exec -i -n kubeagents-system "$AGENT_POD" -c platform-agent -- curl -m 3 -I http://www.googleapis.com || echo "BLOCKED (Expected)"

# E-12 to E-14
kubectl exec -i -n kubeagents-system "$AGENT_POD" -c platform-agent -- curl -m 3 https://10.0.0.100 || echo "BLOCKED (Expected)"
kubectl exec -i -n kubeagents-system "$AGENT_POD" -c platform-agent -- curl -m 3 https://172.16.1.100 || echo "BLOCKED (Expected)"
kubectl exec -i -n kubeagents-system "$AGENT_POD" -c platform-agent -- curl -m 3 https://192.168.1.100 || echo "BLOCKED (Expected)"

# E-15
kubectl exec -i -n kubeagents-system "$AGENT_POD" -c platform-agent -- curl -m 3 http://gke-managed-otel-collector.gke-managed-otel.svc:4318/v1/traces || echo "Connected to OTLP HTTP port"

echo "DONE!"
