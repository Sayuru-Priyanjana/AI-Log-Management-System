#!/bin/bash
if [ "$1" = "cleanup" ]; then
    echo "Cleaning up CrashLoopBackOff incident..."
    sudo kubectl -n incident-test patch deployment payment-api -p '{"spec": {"template": {"spec": {"containers": [{"name": "payment-api", "command": ["/bin/sh", "-c", "pip install prometheus_client && python3 /app/app.py"]}]}}}}'
    exit 0
fi
echo "Starting CrashLoopBackOff incident..."
sudo kubectl -n incident-test patch deployment payment-api -p '{"spec": {"template": {"spec": {"containers": [{"name": "payment-api", "command": ["/bin/sh", "-c", "exit 1"]}]}}}}'
echo "Incident generated. Deployment patched to crash immediately."
