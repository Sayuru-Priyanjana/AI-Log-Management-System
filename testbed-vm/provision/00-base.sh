#!/usr/bin/env bash
set -euo pipefail

echo "==> Base packages"
echo "==> Waiting for apt locks..."
export DEBIAN_FRONTEND=noninteractive
until apt-get update -y; do
  echo "apt locked, waiting 5 seconds..."
  sleep 5
done
apt-get install -y curl jq gettext-base ca-certificates

# k3s stores container logs under /var/log/pods with symlinks in
# /var/log/containers; Fluent Bit tails the latter. Nothing to configure, but we
# make sure the journal does not eat the disk on a 3 GB box.
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/99-logintel.conf <<'EOF'
[Journal]
SystemMaxUse=200M
EOF
systemctl restart systemd-journald || true

# A 3 GB VM running k3s + Prometheus benefits from a little swap headroom so a
# memory-leak incident kills the target container rather than the kubelet.
if [[ ! -f /swapfile ]]; then
  echo "==> Creating 1G swapfile"
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Base provisioning complete"
