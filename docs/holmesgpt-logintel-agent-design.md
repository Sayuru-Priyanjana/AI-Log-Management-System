# HolmesGPT as a separate LogIntel agent

Implementation: see [`holmes-agent/README.md`](../holmes-agent/README.md).
The adapter, gateway selector, PromQL controls, Compose services, and Kubernetes
manifests are now implemented. Live deployment verification is tracked separately.

## Decision

Run HolmesGPT as a third, independent investigation engine. Keep the existing Custom and LangGraph services available. An adapter owned by LogIntel will expose the LogIntel investigation contract and call a pinned HolmesGPT release. The administrator will select the default engine in the **Configuration** page. Store that choice on the server so it applies to every workstation.

This design uses HolmesGPT's investigation loop and toolsets; it does not copy its code into `langgraph-agent`. HolmesGPT is [Apache-2.0 licensed](https://github.com/HolmesGPT/holmesgpt/blob/master/LICENSE). Pin an upstream release or commit, retain its license notices, and test upgrades before changing the pin.

## Current state

- The Configuration page already has an Agent Type dropdown with Custom and LangGraph. It writes `ui.agentBackend` to browser `localStorage`, so different browsers can silently use different agents.
- The gateway reads `x-agent-backend` and routes requests to the Custom or LangGraph URL. Its fallback for any other value is Custom.
- All selected-agent requests currently use the same routing choice, including health, systems, settings, and investigation history. HolmesGPT does not implement those LogIntel endpoints.
- LogIntel investigations use `POST /api/investigations` with `system_id`, `environment`, `question`, optional time/service scope, `thread_id`, and chat history. The response is an NDJSON stream of `{stage, data}` events, ending with a structured `result` that the UI can reopen from history.

## Proposed request path

```text
Browser
  -> gateway authentication and system authorization
  -> selected engine (Custom | LangGraph | Holmes)
       -> LogIntel Holmes adapter: POST /api/investigations
            -> validate system, environment, time range, and service scope
            -> HolmesGPT /api/chat
                 -> LogIntel scoped OpenSearch/Prometheus tools
                 -> optional read-only Kubernetes tools for the chosen cluster
            -> validate evidence and shape the answer
            -> save a LogIntel InvestigationResult
            -> stream LogIntel NDJSON events to the UI

Shared system registry, settings, health, and history reads
  -> canonical LogIntel API, regardless of selected investigation engine
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
- For crashloops, inspect current and previous container logs and termination reasons where Kubernetes access is configured. A rollout timestamp alone is a correlation, not proof of the exit cause.
- Start read-only. Prevent the agent from executing changes or querying other systems through generic shell, unscoped Elasticsearch DSL, or unrestricted Kubernetes tools.
- Limit document counts, time range, tool output size, model steps, and request duration. A 10-million-document index should be reduced by server-side aggregations before text reaches the model.

## Adapter contract

The adapter should accept LogIntel's existing `InvestigationRequest`, then emit the same event names the UI consumes (`plan`, `windows`, `signals`, `reasoning`, `answer`, `result`, `error`). It should build a valid `InvestigationResult` and `StructuredAnswer`, persist it in the shared investigation index, and preserve `thread_id`. It may provide fewer intermediate stages than LangGraph, but every stage it emits must use the same schema. Record HolmesGPT tool calls and model usage where available.

If HolmesGPT offers a conclusion without direct evidence for the specific cause, the adapter must keep the cause unconfirmed and say what evidence is missing. Validate citation IDs against tool results before assigning confidence. This protects the user interface from presenting plausible prose as measured fact.

## Delivery sequence

1. Define the adapter contract with fixture requests and expected NDJSON/result shapes. Confirm that existing LogIntel history and follow-ups can read a Holmes run.
2. Build `holmes-agent` against a pinned upstream version; add a LogIntel-scoped toolset and offline adapter tests.
3. Add server-owned backend selection and thread pinning to the gateway; move the Configuration dropdown from browser preference to the server setting.
4. Add the Holmes service to Compose and `logintel-central` deployment manifests, including its model secret, resource limits, and health checks.
5. Run the three agents side by side against the same fixed incident cases. Compare evidence, cause accuracy, uncertainty, latency, and model cost. Enable Holmes in the selector only after its contract and scoping checks pass.

## Acceptance checks

- An admin's engine choice appears on another workstation and survives a gateway restart.
- A new Holmes investigation is restricted to the selected LogIntel system and produces a readable, persisted result.
- Follow-ups and reopened history stay with the engine that created the thread.
- A failed Holmes service returns a visible error rather than running Custom or LangGraph.
- A crashloop with only a simultaneous deployment change is reported as an unconfirmed exit cause.
- Developer users cannot request another system by changing a header, body field, or Holmes tool argument.
