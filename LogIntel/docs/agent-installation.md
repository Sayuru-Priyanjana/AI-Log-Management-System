# LogIntel Agent Installation Guide

The LogIntel Agent is distributed as an OCI-compatible Helm chart hosted on GitHub Container Registry (GHCR). 
This allows you to easily install the agent into any Kubernetes cluster without copying files manually.

## Prerequisites
- A Kubernetes cluster (v1.20+)
- Helm 3.8.0 or newer
- Network access to `ghcr.io`
- Network access to your Central LogIntel Server

## Public vs Private Registry
If the repository is set to public, you do not need to authenticate. 
If the repository is private, you must first authenticate Helm with GHCR using a Personal Access Token (PAT) with `read:packages` permissions:

```bash
helm registry login ghcr.io -u <your-github-username>
# Enter your PAT when prompted for password
```

## Installation

To install the LogIntel Agent, you only need your Central IP, your Cluster ID, and your Authentication Token (which you can generate from the LogIntel Dashboard).

Run the following command:

```bash
helm install logintel-agent \
  oci://ghcr.io/sayuru-priyanjana/logintel-agent \
  --version 0.1.0 \
  --set central.url="http://YOUR_CENTRAL_IP" \
  --set auth.clusterId="cls-xxxxx" \
  --set auth.token="YOUR_TOKEN"
```

## Upgrading

When a new version of the LogIntel Agent is released, simply run the upgrade command with the new version tag:

```bash
helm upgrade logintel-agent \
  oci://ghcr.io/sayuru-priyanjana/logintel-agent \
  --version 0.2.0 \
  --set central.url="http://YOUR_CENTRAL_IP" \
  --set auth.clusterId="cls-xxxxx" \
  --set auth.token="YOUR_TOKEN"
```

## Uninstallation

To completely remove the agent from your cluster:

```bash
helm uninstall logintel-agent
```

## Security

The agent configuration handles authentication tokens securely. By default, the `auth.token` parameter is injected directly into a Kubernetes Secret. Neither the raw ConfigMap nor the agent container specifications directly expose this token in plain text.
