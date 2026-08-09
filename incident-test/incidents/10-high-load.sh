#!/bin/bash
if [ "$1" = "cleanup" ]; then
    echo "Cleaning up High Load incident..."
    sudo kubectl -n incident-test delete pod loadgen-high --ignore-not-found
    exit 0
fi
echo "Starting High Load incident..."
sudo kubectl -n incident-test run loadgen-high --image=curlimages/curl --restart=Never -- /bin/sh -c 'while true; do curl -s -X POST http://payment-api:8000/api/payment >/dev/null; sleep 0.01; done'
echo "Incident generated."
