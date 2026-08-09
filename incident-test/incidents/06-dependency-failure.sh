#!/bin/bash
if [ "$1" = "cleanup" ]; then
    echo "Cleaning up Dependency Failure incident..."
    sudo kubectl -n incident-test scale deployment payment-db --replicas=1
    sudo kubectl -n incident-test delete pod loadgen-dep --ignore-not-found
    exit 0
fi
echo "Starting Dependency Failure incident..."
sudo kubectl -n incident-test scale deployment payment-db --replicas=0
sudo kubectl -n incident-test run loadgen-dep --image=curlimages/curl --restart=Never -- /bin/sh -c 'while true; do curl -s -X POST http://payment-api:8000/api/payment >/dev/null; sleep 0.2; done'
echo "Incident generated."
