#!/usr/bin/env bash
set -e
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

echo "Deploying logintel-agent via Helm..."

# The logintel-agent folder is synced from the host to /logintel-agent
# The host IP from Vagrant is 192.168.56.1
# Port 9200 for OpenSearch, Port 9090 for Central Prometheus

helm upgrade --install logintel-agent /logintel-agent \
  --namespace logintel \
  --create-namespace \
  --set namespace="logintel" \
  --set auth.clusterId="friend-cluster-1" \
  --set auth.token="logintel_tok_1234567890" \
  --set central.url="http://192.168.56.1:3000" \
  --set central.prometheus_url="http://192.168.56.1:3000/api/v1/write"

echo "Waiting for pods to be ready..."
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=logintel-agent -n logintel --timeout=120s || true

echo "Deployment finished."
