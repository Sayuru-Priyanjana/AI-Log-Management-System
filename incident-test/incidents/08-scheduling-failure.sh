#!/bin/bash
if [ "$1" = "cleanup" ]; then
    echo "Cleaning up Scheduling Failure incident..."
    sudo kubectl -n incident-test delete deployment impossible-deployment --ignore-not-found
    exit 0
fi
echo "Starting Scheduling Failure incident..."
cat <<EOF | sudo kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: impossible-deployment
  namespace: incident-test
spec:
  replicas: 1
  selector:
    matchLabels:
      app: impossible
  template:
    metadata:
      labels:
        app: impossible
    spec:
      containers:
      - name: impossible
        image: nginx
        resources:
          requests:
            cpu: 1000
EOF
echo "Incident generated."
