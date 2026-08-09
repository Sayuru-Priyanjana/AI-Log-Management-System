import os

os.makedirs(r'd:\Projects\AI-Log-Management-System\incident-test\incidents', exist_ok=True)

scripts = {
    '01-cpu-saturation.sh': '''#!/bin/bash
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
''',
    
    '02-oomkill.sh': '''#!/bin/bash
if [ "$1" = "cleanup" ]; then
    echo "Cleaning up OOMKill incident..."
    sudo kubectl -n incident-test exec deployment/payment-api -- rm -f /tmp/oom 2>/dev/null || true
    exit 0
fi
echo "Starting OOMKill incident..."
sudo kubectl -n incident-test exec deployment/payment-api -- touch /tmp/oom
echo "Incident generated. The payment-api will consume memory until OOMKilled."
''',

    '03-crashloop.sh': '''#!/bin/bash
if [ "$1" = "cleanup" ]; then
    echo "Cleaning up CrashLoopBackOff incident..."
    sudo kubectl -n incident-test patch deployment payment-api -p '{"spec": {"template": {"spec": {"containers": [{"name": "payment-api", "command": ["/bin/sh", "-c", "pip install prometheus_client && python3 /app/app.py"]}]}}}}'
    exit 0
fi
echo "Starting CrashLoopBackOff incident..."
sudo kubectl -n incident-test patch deployment payment-api -p '{"spec": {"template": {"spec": {"containers": [{"name": "payment-api", "command": ["/bin/sh", "-c", "exit 1"]}]}}}}'
echo "Incident generated. Deployment patched to crash immediately."
''',

    '04-readiness-failure.sh': '''#!/bin/bash
if [ "$1" = "cleanup" ]; then
    echo "Cleaning up Readiness Failure incident..."
    sudo kubectl -n incident-test exec deployment/payment-api -- rm -f /tmp/unhealthy
    exit 0
fi
echo "Starting Readiness Failure incident..."
sudo kubectl -n incident-test exec deployment/payment-api -- touch /tmp/unhealthy
echo "Incident generated."
''',

    '05-http500-burst.sh': '''#!/bin/bash
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
''',

    '06-dependency-failure.sh': '''#!/bin/bash
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
''',

    '07-pod-restart.sh': '''#!/bin/bash
if [ "$1" = "cleanup" ]; then
    echo "Nothing to clean up for Pod Restart."
    exit 0
fi
echo "Starting Pod Restart incident..."
sudo kubectl -n incident-test delete pod -l app=payment-api
echo "Incident generated."
''',

    '08-scheduling-failure.sh': '''#!/bin/bash
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
''',

    '09-network-dependency-failure.sh': '''#!/bin/bash
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
''',

    '10-high-load.sh': '''#!/bin/bash
if [ "$1" = "cleanup" ]; then
    echo "Cleaning up High Load incident..."
    sudo kubectl -n incident-test delete pod loadgen-high --ignore-not-found
    exit 0
fi
echo "Starting High Load incident..."
sudo kubectl -n incident-test run loadgen-high --image=curlimages/curl --restart=Never -- /bin/sh -c 'while true; do curl -s -X POST http://payment-api:8000/api/payment >/dev/null; sleep 0.01; done'
echo "Incident generated."
'''
}

for name, content in scripts.items():
    with open(rf'd:\Projects\AI-Log-Management-System\incident-test\incidents\{name}', 'w', newline='\n') as f:
        f.write(content)
