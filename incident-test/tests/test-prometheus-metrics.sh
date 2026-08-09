#!/bin/bash
set -e

PROM_URL="http://localhost:30090"
echo "=== Testing Prometheus Metrics Verification ==="

function check_metric() {
    local metric_query=$1
    local name=$2
    local result=$(curl -s -G "$PROM_URL/api/v1/query" --data-urlencode "query=$metric_query")
    
    if echo "$result" | grep -q '"result":\[{'; then
        echo "[OK] $name found."
    else
        echo "[FAILED] $name NOT found! Query used: $metric_query"
        echo "Debug output: $result"
        exit 1
    fi
}

echo "Testing Application Metrics..."
check_metric "http_requests_total{service='payment-api'}" "Application Request Rate"
check_metric "payment_failures_total{service='payment-api'}" "Application Error Rate"
check_metric "http_request_duration_seconds_count{service='payment-api'}" "Application Latency"

echo "Testing Container/Pod Metrics (cAdvisor)..."
check_metric "container_cpu_usage_seconds_total{namespace='incident-test'}" "Pod CPU"
check_metric "container_memory_working_set_bytes{namespace='incident-test'}" "Pod Memory"

echo "Testing Kubernetes State Metrics (kube-state-metrics)..."
check_metric "kube_pod_container_status_restarts_total{container='payment-api'}" "Pod Restart Count"
check_metric "kube_pod_status_ready{pod=~'payment-api.*'}" "Pod Readiness"

echo "Testing Node Metrics (node-exporter)..."
check_metric "node_cpu_seconds_total" "Node CPU"
check_metric "node_memory_MemTotal_bytes" "Node Memory"

echo -e "\nAll metrics successfully verified and ready for AI Agent!"
