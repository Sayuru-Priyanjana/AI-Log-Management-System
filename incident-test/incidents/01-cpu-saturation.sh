#!/bin/bash
if [ "$1" = "cleanup" ]; then
    echo "Cleaning up CPU saturation incident..."
    sudo kubectl -n incident-test exec deployment/payment-api -- rm -f /tmp/high_cpu
    sudo kubectl -n incident-test delete pod loadgen-cpu --ignore-not-found
    exit 0
fi
echo "Starting CPU saturation incident..."
sudo kubectl -n incident-test exec deployment/payment-api -- touch /tmp/high_cpu
sudo kubectl -n incident-test run loadgen-cpu --image=curlimages/curl --restart=Never -- /bin/sh -c 'while true; do curl -s -X POST http://payment-api:8000/api/payment >/dev/null; sleep 0.05; done'
echo "Incident generated."
