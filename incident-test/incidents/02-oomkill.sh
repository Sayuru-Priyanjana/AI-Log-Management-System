#!/bin/bash
if [ "$1" = "cleanup" ]; then
    echo "Cleaning up OOMKill incident..."
    sudo kubectl -n incident-test exec deployment/payment-api -- rm -f /tmp/oom 2>/dev/null || true
    exit 0
fi
echo "Starting OOMKill incident..."
sudo kubectl -n incident-test exec deployment/payment-api -- touch /tmp/oom
echo "Incident generated. The payment-api will consume memory until OOMKilled."
