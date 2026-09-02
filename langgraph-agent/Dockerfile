# The agent, and only the agent.
#
# Everything it analyses lives outside this image on purpose. OpenSearch holds
# the logs and events, Prometheus holds the metrics, and the model runs in
# Ollama — three services with their own lifecycles, their own storage and, in
# Ollama's case, access to a GPU this container has no business claiming. They
# are reached over the network and configured by environment variable, so the
# same image runs against a laptop's local stack or a cluster's shared one.
#
#   docker build -t log-agent ./agent
#   docker run --rm -p 8000:8000 --env-file agent/.env log-agent
#
# On Docker Desktop, `host.docker.internal` reaches services on the host; on
# Linux add `--add-host=host.docker.internal:host-gateway`.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first: they change far less often than the source, so an edit to
# a pipeline module reuses this layer instead of reinstalling the world.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY eval ./eval

# Runs unprivileged. Nothing here writes to disk — investigations are persisted
# to OpenSearch — so the filesystem can stay owned by root and read-only.
RUN useradd --create-home --uid 10001 agent
USER agent

EXPOSE 8000

# Defaults that assume the external services are on the Docker host. Every one
# of them is overridable at run time; none is baked into the code.
ENV OPENSEARCH_URL=http://host.docker.internal:9200 \
    PROMETHEUS_URL=http://host.docker.internal:30090 \
    OLLAMA_BASE_URL=http://host.docker.internal:11434 \
    INCIDENT_CONTROLLER_URL=http://host.docker.internal:30099 \
    LLM_PROVIDER=ollama \
    LOG_LEVEL=INFO

# The container is healthy when the process answers, not when every dependency
# is reachable — a missing Prometheus is a degraded investigation, not a dead
# agent, and /api/health reports that difference in its body.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
