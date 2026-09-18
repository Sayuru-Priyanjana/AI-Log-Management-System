#!/usr/bin/env bash
set -euo pipefail

REGISTRY="${REGISTRY:-ghcr.io/sayuru-priyanjana}"
TARGET="${1:-all}"
TAG="${2:-manual-$(date -u +%Y%m%d%H%M%S)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
BUILDER_DRIVER="${BUILDER_DRIVER:-docker-container}"
BUILDER_NAME="${BUILDER_NAME:-multi-arch-builder}"
LANGGRAPH_IMAGE="${LANGGRAPH_IMAGE:-ai-agent-langgraph}"
GATEWAY_IMAGE="${GATEWAY_IMAGE:-logintel-gateway}"
UI_IMAGE="${UI_IMAGE:-logintel-ui}"

case "$TARGET" in
  all|langgraph|gateway|ui|langgraph-stack) ;;
  *) echo "Usage: $0 [all|langgraph|gateway|ui|langgraph-stack] [tag]" >&2; exit 2 ;;
esac

if [[ "$BUILDER_DRIVER" == "docker-container" ]]; then docker info >/dev/null; fi
if ! docker buildx inspect "$BUILDER_NAME" >/dev/null 2>&1; then
  if [[ "$BUILDER_DRIVER" == "kubernetes" ]]; then
    docker buildx create --driver kubernetes \
      --driver-opt "namespace=${BUILDER_NAMESPACE:-logintel-central}" \
      --name "$BUILDER_NAME" --use
  else
    docker buildx create --driver "$BUILDER_DRIVER" --name "$BUILDER_NAME" --use
  fi
fi
docker buildx use "$BUILDER_NAME"

build_push() {
  local image="$1" context="$2" target="${3:-}"
  local args=(build --platform linux/arm64 -t "$REGISTRY/$image:$TAG" --push)
  if [[ -n "$target" ]]; then args+=(--target "$target"); fi
  args+=("$REPO_ROOT/$context")
  echo "Building and pushing $REGISTRY/$image:$TAG"
  docker buildx "${args[@]}"
}

if [[ "$TARGET" == all ]]; then
  build_push logintel-ai-agent agent
fi
if [[ "$TARGET" == all || "$TARGET" == langgraph || "$TARGET" == langgraph-stack ]]; then
  build_push "$LANGGRAPH_IMAGE" langgraph-agent
fi
if [[ "$TARGET" == all || "$TARGET" == gateway || "$TARGET" == langgraph-stack ]]; then
  build_push "$GATEWAY_IMAGE" gateway
fi
if [[ "$TARGET" == all || "$TARGET" == ui || "$TARGET" == langgraph-stack ]]; then
  build_push "$UI_IMAGE" ui serve
fi
if [[ "$TARGET" == all ]]; then
  build_push logintel-metrics-mirror metrics-mirror
fi

echo "Published tag: $TAG"
