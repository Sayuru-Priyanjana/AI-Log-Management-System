#!/bin/bash
if [ "$1" = "cleanup" ]; then
    echo "Nothing to clean up for Pod Restart."
    exit 0
fi
echo "Starting Pod Restart incident..."
sudo kubectl -n incident-test delete pod -l app=payment-api
echo "Incident generated."
