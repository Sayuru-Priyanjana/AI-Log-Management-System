#!/bin/bash
set -e

REGISTRY="ghcr.io/sayuru-priyanjana"
TAG="latest"

echo "Log in to GHCR before running this script if you haven't already:"
echo "echo \$CR_PAT | docker login ghcr.io -u sayuru-priyanjana --password-stdin"
echo ""

echo "Setting up Docker Buildx for multi-arch builds..."
docker buildx create --use --name multi-arch-builder || true

# Change to the root directory where the source code actually lives
cd ../../../

echo "Building and pushing logintel-agent (ARM64)..."
docker buildx build --platform linux/arm64 -t $REGISTRY/logintel-agent:$TAG --push ./agent

echo "Building and pushing logintel-gateway (ARM64)..."
docker buildx build --platform linux/arm64 -t $REGISTRY/logintel-gateway:$TAG --push ./gateway

echo "Building and pushing logintel-ui (ARM64)..."
docker buildx build --platform linux/arm64 --target serve -t $REGISTRY/logintel-ui:$TAG --push ./ui

echo "Building and pushing logintel-metrics-mirror (ARM64)..."
docker buildx build --platform linux/arm64 -t $REGISTRY/logintel-metrics-mirror:$TAG --push ./metrics-mirror

echo "All images built and pushed successfully!"
