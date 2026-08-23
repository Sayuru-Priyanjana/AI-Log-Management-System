#!/usr/bin/env bash
# Runs on every `vagrant up`. Reports the health of each moving part so a
# partially-wired testbed is obvious here rather than three steps later.
set -uo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

: "${OPENSEARCH_HOST:=192.168.56.1}"
: "${OPENSEARCH_PORT:=9200}"

echo "Waiting for Kubernetes API..."
for _ in $(seq 1 30); do
  if kubectl get nodes >/dev/null 2>&1; then break; fi
  sleep 2
done

echo "Waiting for endpoints to be ready..."
for _ in $(seq 1 60); do
  PROM_OK=0
  INC_OK=0
  curl -fsS -m 2 "http://localhost:30090/-/healthy" >/dev/null 2>&1 && PROM_OK=1
  curl -fsS -m 2 "http://localhost:30099/incidents" >/dev/null 2>&1 && INC_OK=1
  if [ "$PROM_OK" -eq 1 ] && [ "$INC_OK" -eq 1 ]; then
    break
  fi
  sleep 2
done

green() { printf '\033[32m  [ok]\033[0m %s\n' "$*"; }
red()   { printf '\033[31m  [--]\033[0m %s\n' "$*"; }

echo ""
echo "================ LogIntel testbed status ================"

echo "Pods:"
kubectl get pods -A --no-headers 2>/dev/null | awk '{printf "  %-20s %-38s %-10s %s\n", $1, $2, $4, $5}'

echo ""
echo "Endpoints:"
curl -fsS -m 5 "http://localhost:30090/-/healthy" >/dev/null 2>&1 \
  && green "Prometheus   http://192.168.56.20:30090  (also localhost:30090 from Windows)" \
  || red   "Prometheus   not answering on :30090"

curl -fsS -m 5 "http://localhost:30099/incidents" >/dev/null 2>&1 \
  && green "Incidents    http://192.168.56.20:30099" \
  || red   "Incidents    not answering on :30099"

curl -fsS -m 5 "http://${OPENSEARCH_HOST}:${OPENSEARCH_PORT}" >/dev/null 2>&1 \
  && green "OpenSearch   http://${OPENSEARCH_HOST}:${OPENSEARCH_PORT} (in WSL, via portproxy)" \
  || red   "OpenSearch   unreachable at ${OPENSEARCH_HOST}:${OPENSEARCH_PORT} — run scripts/setup-windows-network.ps1 as Administrator in Windows"

echo ""
echo "Documents delivered so far:"
for idx in logintel-logs logintel-events; do
  n=$(curl -fsS -m 5 "http://${OPENSEARCH_HOST}:${OPENSEARCH_PORT}/${idx}-*/_count" 2>/dev/null | jq -r '.count // "?"' 2>/dev/null)
  printf "  %-18s %s\n" "${idx}-*" "${n:-unreachable}"
done

echo ""
echo "Inject an incident:"
echo "  curl -X POST http://192.168.56.20:30099/incidents/dependency-outage/start"
echo "========================================================="
echo ""
