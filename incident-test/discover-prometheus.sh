#!/bin/bash
set -e

PROM_URL="http://localhost:30090"
echo "=== Discovering Prometheus Environment ==="
echo "URL: $PROM_URL"

echo -e "\n=== Scrape Targets ==="
curl -s "$PROM_URL/api/v1/targets" | grep -o '"job":"[^"]*"' | sort | uniq -c

echo -e "\n=== Available Metrics (Sample) ==="
curl -s "$PROM_URL/api/v1/label/__name__/values" | grep -o '"[a-zA-Z_]*"' | head -n 20
echo "..."

echo -e "\n=== Checking for Kube State Metrics ==="
curl -s "$PROM_URL/api/v1/query?query=kube_pod_status_ready" | grep -q "result" && echo "kube_pod_status_ready: FOUND" || echo "kube_pod_status_ready: NOT FOUND"

echo -e "\n=== Checking for cAdvisor Metrics ==="
curl -s "$PROM_URL/api/v1/query?query=container_cpu_usage_seconds_total" | grep -q "result" && echo "container_cpu_usage_seconds_total: FOUND" || echo "container_cpu_usage_seconds_total: NOT FOUND"

echo -e "\n=== Checking for Application Metrics ==="
curl -s "$PROM_URL/api/v1/query?query=http_requests_total" | grep -q "result" && echo "http_requests_total: FOUND" || echo "http_requests_total: NOT FOUND"
