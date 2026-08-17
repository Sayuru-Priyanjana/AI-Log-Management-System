#!/usr/bin/env bash
set -euo pipefail

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
APPS=/vagrant/apps
RENDER=/tmp/logintel-render

: "${OPENSEARCH_HOST:=192.168.56.1}"
: "${OPENSEARCH_PORT:=9200}"

rm -rf "${RENDER}" && mkdir -p "${RENDER}"

echo "==> Building ConfigMaps from apps/"
kubectl create namespace shopdemo --dry-run=client -o yaml > "${RENDER}/ns-shopdemo.yaml"
kubectl create configmap service-src \
  --from-file="${APPS}/service.py" \
  --from-file="${APPS}/loadgen.py" \
  --namespace shopdemo --dry-run=client -o yaml > "${RENDER}/cm-service-src.yaml"

kubectl create namespace logintel-system --dry-run=client -o yaml > "${RENDER}/ns-system.yaml"
kubectl create configmap event-collector-src \
  --from-file="${APPS}/event_collector.py" \
  --namespace logintel-system --dry-run=client -o yaml > "${RENDER}/cm-event-src.yaml"
kubectl create configmap incident-controller-src \
  --from-file="${APPS}/incident_controller.py" \
  --namespace logintel-system --dry-run=client -o yaml > "${RENDER}/cm-incident-src.yaml"

kubectl apply -f "${RENDER}/ns-shopdemo.yaml"
kubectl apply -f "${RENDER}/ns-system.yaml"
kubectl apply -f "${RENDER}/cm-service-src.yaml"
kubectl apply -f "${RENDER}/cm-event-src.yaml"
kubectl apply -f "${RENDER}/cm-incident-src.yaml"

echo "==> Rendering manifests"
export OPENSEARCH_HOST OPENSEARCH_PORT
envsubst '${OPENSEARCH_HOST} ${OPENSEARCH_PORT}' < /vagrant/manifests/30-platform.yaml > "${RENDER}/30-platform.yaml"
cp /vagrant/manifests/40-workload.yaml "${RENDER}/40-workload.yaml"

echo "==> Applying manifests"
kubectl apply -f "${RENDER}/30-platform.yaml"
kubectl apply -f "${RENDER}/40-workload.yaml"

echo "==> Waiting for workload rollout"
kubectl -n shopdemo        rollout status deploy/payment-db          --timeout=300s || true
kubectl -n shopdemo        rollout status deploy/payment-api         --timeout=300s || true
kubectl -n shopdemo        rollout status deploy/checkout-api        --timeout=300s || true
kubectl -n shopdemo        rollout status deploy/loadgen             --timeout=300s || true
kubectl -n logintel-system rollout status deploy/event-collector     --timeout=300s || true
kubectl -n logintel-system rollout status deploy/incident-controller --timeout=300s || true

echo "==> Deploy complete"
kubectl get pods -A
