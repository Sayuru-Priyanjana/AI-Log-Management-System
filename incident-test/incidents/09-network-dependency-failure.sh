#!/bin/bash
if [ "$1" = "cleanup" ]; then
    echo "Cleaning up Network Failure incident..."
    sudo kubectl -n incident-test delete networkpolicy deny-db --ignore-not-found
    sudo kubectl -n incident-test delete pod loadgen-net --ignore-not-found
    exit 0
fi
echo "Starting Network Failure incident..."
cat <<EOF | sudo kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-db
  namespace: incident-test
spec:
  podSelector:
    matchLabels:
      app: payment-db
  policyTypes:
  - Ingress
  ingress: []
EOF
sudo kubectl -n incident-test run loadgen-net --image=curlimages/curl --restart=Never -- /bin/sh -c 'while true; do curl -s -X POST http://payment-api:8000/api/payment >/dev/null; sleep 0.2; done'
echo "Incident generated."
