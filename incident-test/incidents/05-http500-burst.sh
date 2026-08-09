#!/bin/bash
if [ "$1" = "cleanup" ]; then
    echo "Cleaning up HTTP 500 burst incident..."
    sudo kubectl -n incident-test exec deployment/payment-api -- rm -f /tmp/force_500
    sudo kubectl -n incident-test delete pod loadgen-500 --ignore-not-found
    exit 0
fi
echo "Starting HTTP 500 burst incident..."
sudo kubectl -n incident-test exec deployment/payment-api -- touch /tmp/force_500
sudo kubectl -n incident-test run loadgen-500 --image=curlimages/curl --restart=Never -- /bin/sh -c 'while true; do curl -s -X POST http://payment-api:8000/api/payment >/dev/null; sleep 0.1; done'
echo "Incident generated."
