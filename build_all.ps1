$REGISTRY="ghcr.io/sayuru-priyanjana"
$TAG="latest"

docker buildx use multi-arch-builder

Write-Host "Building logintel-ai-agent..."
docker buildx build --platform linux/arm64 -t "$REGISTRY/logintel-ai-agent:$TAG" --push ./agent
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Building ai-agent-langgraph..."
docker buildx build --platform linux/arm64 -t "$REGISTRY/ai-agent-langgraph:$TAG" --push ./langgraph-agent
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Building logintel-gateway..."
docker buildx build --platform linux/arm64 -t "$REGISTRY/logintel-gateway:$TAG" --push ./gateway
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Building logintel-ui..."
docker buildx build --platform linux/arm64 --target serve -t "$REGISTRY/logintel-ui:$TAG" --push ./ui
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Building logintel-metrics-mirror..."
docker buildx build --platform linux/arm64 -t "$REGISTRY/logintel-metrics-mirror:$TAG" --push ./metrics-mirror
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "All builds finished successfully!"
