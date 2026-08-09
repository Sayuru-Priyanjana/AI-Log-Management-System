#!/bin/bash
if [ "$1" = "cleanup" ]; then
    echo "Cleaning up Readiness Failure incident..."
    sudo kubectl -n incident-test exec deployment/payment-api -- rm -f /tmp/unhealthy
    exit 0
fi
echo "Starting Readiness Failure incident..."
sudo kubectl -n incident-test exec deployment/payment-api -- touch /tmp/unhealthy
echo "Incident generated."
