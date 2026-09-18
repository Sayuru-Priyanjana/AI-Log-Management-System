# LangGraph service architecture and playbooks

## Admin workflow

Open **Systems → Architecture & playbooks** for a registered cluster. Choose an
environment and add Kubernetes namespaces in the sidebar. Each namespace gets
its own lane; older maps open with their services in the Unassigned lane.
Choose a namespace when adding a discovered or manually named service. Use
**Move** to drag a service within or between lanes, **Pan** to navigate, and
the **+**, **−**, mouse wheel, or **Fit** controls to zoom. Use **Connect** to
drag an arrow from a caller service to its dependency. Selecting a service
also lets an admin assign its namespace or remove links. Removing a namespace
moves its services to Unassigned and preserves their playbooks and links.
Select a service to attach
playbooks with signal types, investigation checks, and Prometheus metric hints.
Save a draft, then **Publish** it. Only the published revision affects new
LangGraph investigations. Drafts and published revisions are separate for each
system and environment in the `logintel-architecture` OpenSearch index. The
gateway allows only admins to access these editing routes and always forwards
them to LangGraph, regardless of the currently selected investigation backend.

## What the agent does

The registry still discovers services from logs. Published map services are
also valid manual selections, including services that have not emitted a log
yet. A manual selection opens the reasoning loop with that service's measured
signals and explicitly keeps it as the answer's subject. Other services remain
available when they can explain a dependency failure or impact.

After signal detection, the graph matches published playbooks to the selected
service, or to affected services if none was selected. Signal types in a
playbook narrow the match; an empty list matches any type. Checks and expected
edges are presented as **admin guidance**, not observed evidence. The agent
must still verify a hypothesis against logs, Kubernetes events, or metrics.
The graph streams a `playbooks` event showing which guidance matched.

For Gemini, the reasoning loop now sends native function declarations and
accepts a `submit_answer` function call. Other LLM providers retain the JSON
action protocol. This removes JSON text parsing from the Gemini tool loop and
keeps model request telemetry and caching counts.

The `query_prometheus` tool lets the model select any Prometheus metric name
and a limited operation (`raw`, `rate`, `increase`, `avg_over_time`, or
`max_over_time`). The server builds the PromQL expression, inserts the exact
system ID, limits namespaces to the selected system, applies the incident time
window, and caps queries and returned series. The model cannot send a raw
PromQL expression. Returned series receive evidence IDs that the answer
verifier can resolve. Playbook metric hints are metric names that guide the
model to this tool.

## API and deployment

Admin routes:

- `GET /api/systems/{id}/architecture?environment={env}` reads draft and published revisions.
- `PUT /api/systems/{id}/architecture?environment={env}` saves a validated draft.
- `POST /api/systems/{id}/architecture/publish?environment={env}` publishes it.

`LogIntel/kubernetes/central/build-and-push.sh langgraph-stack TAG` builds
and pushes the ARM64 LangGraph agent, gateway, and UI images to GHCR without
CI. The script also accepts individual targets and image-name overrides. The
central manifest pins the deployed `manual-20260917-231108` tag for LangGraph
and UI. Alertmanager
automation and Teams action buttons remain deferred.
