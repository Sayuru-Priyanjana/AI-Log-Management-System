#!/usr/bin/env bash
set -euo pipefail

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
MANIFESTS=/vagrant/manifests
APPS=/vagrant/apps
RENDER=/tmp/logintel-render

: "${OPENSEARCH_HOST:=192.168.56.1}"
: "${OPENSEARCH_PORT:=9200}"

echo "==> OpenSearch target: ${OPENSEARCH_HOST}:${OPENSEARCH_PORT}"
if curl -fsS -m 5 "http://${OPENSEARCH_HOST}:${OPENSEARCH_PORT}" >/dev/null 2>&1; then
  echo "    reachable"
else
  echo "    NOT reachable yet — collectors will retry once it comes up."
  echo "    On Windows (elevated): scripts\\setup-windows-network.ps1"
fi

rm -rf "${RENDER}" && mkdir -p "${RENDER}"

# The application source lives in apps/ as real, readable files rather than being
# buried inside YAML heredocs. We fold them into ConfigMaps at deploy time so
# they stay editable and diffable.
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
for f in "${MANIFESTS}"/*.yaml; do
  envsubst '${OPENSEARCH_HOST} ${OPENSEARCH_PORT}' < "$f" > "${RENDER}/$(basename "$f")"
done

echo "==> Applying manifests"
for f in "${RENDER}"/[0-9]*.yaml; do
  echo "    $(basename "$f")"
  kubectl apply -f "$f"
done

echo "==> Waiting for workload rollout (this is the slow part on first boot)"
kubectl -n shopdemo        rollout status deploy/payment-db          --timeout=300s || true
kubectl -n shopdemo        rollout status deploy/payment-api         --timeout=300s || true
kubectl -n shopdemo        rollout status deploy/checkout-api        --timeout=300s || true
kubectl -n shopdemo        rollout status deploy/loadgen             --timeout=300s || true
kubectl -n logintel-system rollout status deploy/prometheus          --timeout=300s || true
kubectl -n logintel-system rollout status deploy/event-collector     --timeout=300s || true
kubectl -n logintel-system rollout status deploy/incident-controller --timeout=300s || true
kubectl -n logintel-system rollout status ds/fluent-bit              --timeout=300s || true

echo "==> Deploy complete"
kubectl get pods -A
