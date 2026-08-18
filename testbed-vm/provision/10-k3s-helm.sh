#!/usr/bin/env bash
set -e

# 1. Install k3s
export INSTALL_K3S_EXEC="--disable traefik"
curl -sfL https://get.k3s.io | sh -

echo "Waiting for k3s to generate kubeconfig..."
while [ ! -f /etc/rancher/k3s/k3s.yaml ]; do
  sleep 2
done

mkdir -p /home/vagrant/.kube
cp /etc/rancher/k3s/k3s.yaml /home/vagrant/.kube/config
chown -R vagrant:vagrant /home/vagrant/.kube
echo 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml' >> /etc/profile.d/k3s.sh

# 2. Wait for k3s to be ready
echo "Waiting for k3s to be ready..."
sleep 15
until kubectl get nodes; do
    echo "Waiting for k3s nodes..."
    sleep 5
done

# 3. Install Helm
echo "Installing Helm..."
curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3
chmod 700 get_helm.sh
./get_helm.sh
