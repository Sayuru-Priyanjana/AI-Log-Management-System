#!/usr/bin/env bash
set -euo pipefail

if systemctl is-active --quiet k3s; then
  echo "==> k3s already running, skipping install"
else
  echo "==> Installing k3s (traefik and servicelb disabled to save memory)"
  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="\
    --disable traefik \
    --disable servicelb \
    --disable metrics-server \
    --node-name logintel \
    --kubelet-arg=housekeeping-interval=10s" sh -
fi

echo "==> Waiting for the node to become Ready"
for _ in $(seq 1 60); do
  if kubectl get nodes 2>/dev/null | grep -q ' Ready '; then break; fi
  sleep 5
done
kubectl get nodes

# kubectl for the vagrant user without sudo.
mkdir -p /home/vagrant/.kube
cp /etc/rancher/k3s/k3s.yaml /home/vagrant/.kube/config
chown -R vagrant:vagrant /home/vagrant/.kube
chmod 600 /home/vagrant/.kube/config
grep -q KUBECONFIG /home/vagrant/.bashrc || \
  echo 'export KUBECONFIG=$HOME/.kube/config' >> /home/vagrant/.bashrc

echo "==> k3s ready"
