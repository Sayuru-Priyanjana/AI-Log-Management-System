# HolmesGPT as a separate LogIntel agent

Implementation: see [`holmes-agent/README.md`](../holmes-agent/README.md).
The adapter, gateway selector, PromQL controls, Compose services, and Kubernetes
manifests are implemented. The three new images are deployed in
`logintel-central` from commit `717f44c`; the global default remains Custom.

## Decision

Run HolmesGPT as a third, independent investigation engine. Keep the existing Custom and LangGraph services available. An adapter owned by LogIntel will expose the LogIntel investigation contract and call a pinned HolmesGPT release. The administrator will select the default engine in the **Configuration** page. Store that choice on the server so it applies to every workstation.

This design uses HolmesGPT's investigation loop and toolsets; it does not copy its code into `langgraph-agent`. HolmesGPT is [Apache-2.0 licensed](https://github.com/HolmesGPT/holmesgpt/blob/master/LICENSE). Pin an upstream release or commit, retain its license notices, and test upgrades before changing the pin.

## Implemented state

- The Configuration page offers Custom, LangGraph, and HolmesGPT. The admin's choice is stored in Postgres and shared across browsers. The gateway ignores browser backend headers.
- The gateway pins each conversation to its starting backend. It keeps shared systems, settings, and history on the canonical LogIntel API while routing Holmes investigations to the adapter.
- The Holmes adapter exposes the LogIntel investigation, identity, graph, health, and PromQL configuration endpoints. Its runtime runs alongside it in one pod.
- LogIntel investigations use `POST /api/investigations` with `system_id`, `environment`, `question`, optional time/service scope, `thread_id`, and chat history. The response is an NDJSON stream of `{stage, data}` events, ending with a structured `result` that the UI can reopen from history.

## Request path

```text
Browser
  -> gateway authentication and system authorization
  -> selected engine (Custom | LangGraph | Holmes)
       -> LogIntel Holmes adapter: POST /api/investigations
            -> validate system, environment, time range, and service scope
            -> HolmesGPT /api/chat
                 -> LogIntel scoped OpenSearch/Prometheus tools
                 -> ingested Kubernetes events for the chosen system
            -> validate evidence and shape the answer
            -> save a LogIntel InvestigationResult
            -> stream LogIntel NDJSON events to the UI

Shared system registry, settings, and history reads
  -> canonical LogIntel API, regardless of selected investigation engine

Selected engine health and identity
  -> selected agent API
```

HolmesGPT already provides an [HTTP chat API](https://github.com/HolmesGPT/holmesgpt/blob/master/docs/reference/http-api.md) and supports [custom toolsets](https://github.com/HolmesGPT/holmesgpt/blob/master/examples/custom_toolset.yaml). The adapter is necessary because its chat response is not LogIntel's investigation result or stream format.

## Components

| Component | Responsibility |
| --- | --- |
| `holmes-agent/` | LogIntel adapter, versioned HolmesGPT dependency, scoped toolset, response validation, result persistence, health/identity endpoints. |
| HolmesGPT runtime | Model/tool loop; configured only with approved read-only investigation tools initially. |
| Gateway | Own the global engine choice; authenticate, authorize, and route new investigations; pin existing threads to their original engine. |
| Configuration page | Show Custom, LangGraph, and HolmesGPT; fetch and update the server setting; show readiness of each deployed engine. |
| Shared investigation store | Keep one history format and record `engine` and upstream version on each run. |

## Global engine selection

1. Add a single server-owned setting, e.g. `default_agent_backend`, restricted to `custom`, `langgraph`, or `holmes`. An admin-only GET/PUT endpoint returns and changes it. Do not treat a browser header as the source of truth for the global default.
2. Make the gateway choose the backend for a **new** investigation. Record the backend against `thread_id`; follow-ups stay on that backend even when the administrator changes the global default.
3. Send shared registry, settings, health, and history reads to the canonical LogIntel API. Route engine-specific identity, graph metadata, and investigation execution to the selected engine as needed.
4. Expose the chosen engine and version in the response and stored run. A missing or unhealthy Holmes service must produce a clear error; never silently run another engine.

## LogIntel toolset boundaries

- Every tool call must carry an enforced `system_id`, environment, and bounded time window. Derive OpenSearch index and filter from the validated registry, not from model-authored arguments.
- Provide bounded queries for error counts, representative logs, Kubernetes events, deployment changes, and Prometheus metrics. Return evidence IDs alongside compact results so claims can cite actual records.
- For crashloops, use ingested logs and events; a rollout timestamp alone is a correlation, not proof of the exit cause. Direct previous-container log retrieval remains a future cluster credential integration.
- Start read-only. Prevent the agent from executing changes or querying other systems through generic shell, unscoped Elasticsearch DSL, or unrestricted Kubernetes tools.
- Limit document counts, time range, tool output size, model steps, and request duration. A 10-million-document index should be reduced by server-side aggregations before text reaches the model.

## Adapter contract

The adapter should accept LogIntel's existing `InvestigationRequest`, then emit the same event names the UI consumes (`plan`, `windows`, `signals`, `reasoning`, `answer`, `result`, `error`). It should build a valid `InvestigationResult` and `StructuredAnswer`, persist it in the shared investigation index, and preserve `thread_id`. It may provide fewer intermediate stages than LangGraph, but every stage it emits must use the same schema. Record HolmesGPT tool calls and model usage where available.

If HolmesGPT offers a conclusion without direct evidence for the specific cause, the adapter must keep the cause unconfirmed and say what evidence is missing. Validate citation IDs against tool results before assigning confidence. This protects the user interface from presenting plausible prose as measured fact.

## Deployment and verification

1. The adapter has eight passing tests, including result schema, scope enforcement, PromQL result isolation, and crashloop uncertainty. CI builds the adapter, gateway, and UI for ARM64.
2. All three deployments rolled out in `logintel-central` and report one ready replica. The gateway reaches the Holmes health endpoint, and the Holmes runtime answered a synthetic model request.
3. A synthetic Holmes frontend tool call paused and resumed successfully. The custom PromQL endpoint returned its CPU and memory built-ins.
4. A live investigation using centralized logs remains unverified because automatic approval review blocked sending those log contents to Gemini. The global backend was left on Custom pending that check and administrator selection.

## Acceptance checks

- An admin's engine choice appears on another workstation and survives a gateway restart.
- A new Holmes investigation is restricted to the selected LogIntel system and produces a readable, persisted result.
- Follow-ups and reopened history stay with the engine that created the thread.
- A failed Holmes service returns a visible error rather than running Custom or LangGraph.
- A crashloop with only a simultaneous deployment change is reported as an unconfirmed exit cause.
- Developer users cannot request another system by changing a header, body field, or Holmes tool argument.
