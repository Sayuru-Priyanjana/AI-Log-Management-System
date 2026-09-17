# LogIntel HolmesGPT adapter

This service is the LogIntel API boundary for a separate HolmesGPT runtime. It
accepts `POST /api/investigations`, calls HolmesGPT's streaming `/api/chat`,
executes only LogIntel-scoped frontend tools, and writes a LogIntel-compatible
investigation document to the shared OpenSearch history index.

The runtime is pinned to `robustadev/holmes:0.39.0` in Compose and Kubernetes.
`ENABLED_BY_DEFAULT_TOOLSETS` is empty there, and Kubernetes does not mount a
service account token into its pod. The Holmes API is internal; users reach it
through the LogIntel gateway.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOLMES_URL` | `http://holmes-runtime:5050` | Upstream HolmesGPT HTTP API. |
| `HOLMES_MODEL` | `gemini/gemini-3.5-flash-lite` | Model passed to HolmesGPT. |
| `OPENSEARCH_URL` | `http://opensearch:9200` | Central logs, events, saved queries, and investigation history. |
| `PROMETHEUS_URL` | `http://prometheus:9090` | Central Prometheus API. |

The runtime receives `GEMINI_API_KEY` from the existing `logintel-llm` secret.
Its config file sets the same model and a 12-step limit.

## Tools

- `logintel_search_logs`: bounded log samples, optionally narrowed by service,
  pod, level, and text. System, environment, and time filters are always added
  by the adapter.
- `logintel_count_logs`: totals by level and service.
- `logintel_search_events`: bounded Kubernetes event samples.
- `logintel_promql`: runs only a built-in or administrator-saved query ID.
  Built-ins cover CPU and memory by container. Saved templates must contain a
  `system_id="{{system_id}}"` selector. Results whose system label does not
  match the selected system are rejected.

The UI Configuration page stores custom PromQL templates per system. Model
arguments cannot select another system or supply arbitrary PromQL.

## Current limits

- Kubernetes diagnosis uses logs and events already ingested into LogIntel.
  Reading a previous container's logs directly from a remote cluster requires a
  separate, cluster-scoped credential integration.
- HolmesGPT returns prose. The adapter records cited LogIntel document IDs and
  keeps confidence low; when a crashloop has no startup or termination evidence,
  it reports the exit cause as unconfirmed.
- The Holmes runtime and adapter must both be healthy before administrators can
  choose HolmesGPT as the global backend.

Run the adapter tests with `python -m unittest -v test_app.py` from this folder.
