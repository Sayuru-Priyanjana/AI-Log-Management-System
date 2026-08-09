# AI-Powered Enterprise Log Analysis System

## Phase 3 — Evidence Correlation Engine

### Technical Design Document

---

## 1. Overview

The AI-Powered Enterprise Log Analysis System collects operational data from multiple sources and uses this information to investigate incidents affecting applications and systems.

The system treats related application components as a single logical system. For example:

```text
E-Commerce Platform
├── Frontend
├── Backend
├── Payment API
├── Order API
└── Database
```

The user selects a system before starting an investigation.

The Orchestrator Agent interprets the user's question and creates an investigation plan. The Dispatcher then retrieves the required evidence from independent data sources:

- Application logs
- Kubernetes events
- Metrics

Phase 3 introduces the **Evidence Correlation Engine**.

The purpose of the Correlation Engine is to transform these independent evidence sources into a single chronological and structurally correlated investigation context.

The Correlation Engine is deliberately implemented as a **deterministic Python component**, rather than an LLM agent.

---

# 2. Current System Architecture

The system currently follows this architecture:

```text
                           User
                            │
                            ▼
                         React UI
                            │
                            ▼
                          FastAPI
                            │
                            ▼
                    Orchestrator Agent
                            │
                            ▼
                    Investigation Plan
                            │
                            ▼
                        Dispatcher
                            │
             ┌──────────────┼──────────────┐
             ▼              ▼              ▼
      Application Logs   K8s Events      Metrics
             │              │              │
             ▼              ▼              ▼
         OpenSearch      OpenSearch     Prometheus
```

The three tools independently retrieve evidence.

The result is:

```text
InvestigationEvidence
```

Phase 3 adds:

```text
InvestigationEvidence
             │
             ▼
      Correlation Engine
             │
             ▼
     CorrelatedEvidence
```

---

# 3. Purpose of the Correlation Engine

Logs, events, and metrics are useful individually, but incident investigation requires understanding how they relate in time and infrastructure.

For example:

```text
10:05:01  Application ERROR
          Database connection timeout

10:05:08  Kubernetes Event
          BackOff restarting container

10:05:10  Metric
          CPU usage = 92%

10:05:15  Metric
          Restart count increased
```

These records originate from different systems and data stores.

The Correlation Engine combines them into a unified investigation timeline.

It does not determine the root cause.

Instead, it answers questions such as:

- What happened first?
- What happened immediately afterward?
- Which events occurred close together?
- Which records belong to the same service?
- Which records belong to the same pod?
- Did resource usage change during the incident?
- Did pod restarts increase?
- Were there bursts of errors?
- Which evidence items are temporally related?

The future Analysis Agent will use this information to determine the likely cause.

---

# 4. Design Principle

The most important architectural principle is:

> **Correlation is deterministic; interpretation is performed by the AI.**

Therefore:

```text
Logs + Events + Metrics
          │
          ▼
 Python Correlation Engine
          │
          ▼
Structured Correlated Evidence
          │
          ▼
      Qwen / LLM
          │
          ▼
Reasoning and Root Cause Analysis
```

The Correlation Engine does not use:

- Qwen
- Ollama
- LangGraph
- embeddings
- vector databases
- RAG
- AI-generated PromQL
- AI-generated OpenSearch queries

---

# 5. Evidence Sources

The Correlation Engine receives three primary evidence types.

## 5.1 Application Logs

Application logs are stored in OpenSearch using the application's normalized schema.

Example:

```json
{
  "@timestamp": "2026-08-09T10:05:01Z",
  "system": {
    "id": "ecommerce-platform",
    "name": "E-Commerce Platform"
  },
  "environment": "production",
  "service": {
    "name": "payment-api",
    "type": "backend",
    "version": "2.4.1"
  },
  "kubernetes": {
    "namespace": "payment",
    "pod": {
      "name": "payment-api-abc"
    },
    "node": {
      "name": "worker-03"
    }
  },
  "log": {
    "level": "ERROR",
    "message": "Database connection timeout"
  }
}
```

Important correlation fields include:

```text
timestamp
system
environment
service
namespace
pod
container
node
log level
event category
event action
trace ID
request ID
error information
HTTP information
```

---

# 6. Kubernetes Events

Kubernetes events provide infrastructure-level information.

Examples include:

```text
BackOff
OOMKilled
Failed
Unhealthy
FailedScheduling
Killing
Started
Pulled
```

Example:

```json
{
  "timestamp": "2026-08-09T10:05:08Z",
  "type": "Warning",
  "reason": "BackOff",
  "namespace": "payment",
  "pod": "payment-api-abc",
  "message": "Back-off restarting failed container"
}
```

Kubernetes events are especially important for identifying:

- container restarts
- scheduling failures
- readiness failures
- OOM conditions
- image failures
- node-related problems

---

# 7. Metrics

Metrics provide quantitative information about the system.

Examples include:

```text
CPU usage
Memory usage
Pod restart count
Pod readiness
Network traffic
HTTP request rate
HTTP error rate
Request latency
```

Metrics are retrieved from Prometheus through the Metrics Tool.

Unlike logs and events, metrics are usually continuous time-series data.

Therefore, the Correlation Engine must handle both:

```text
individual evidence events
```

and:

```text
metric time-series
```

---

# 8. Normalization

The three evidence sources have different schemas.

To correlate them, the system converts them into a common internal representation.

The normalized structure contains:

```text
ID
Timestamp
Source type
System
Environment
Service
Namespace
Pod
Container
Node
Severity
Event type
Event category
Event action
Title
Message
Metric name
Metric value
Metric unit
Metadata
```

Conceptually:

```text
Application Log
       │
       ├── timestamp
       ├── service
       ├── pod
       └── message
              │
              ▼
       TimelineEvidence
              ▲
              │
       Kubernetes Event
              │
       Metric Sample
```

This allows the correlation engine to process all evidence consistently.

---

# 9. Common Evidence Model

A common internal model can be represented as:

```python
class TimelineEvidence(BaseModel):

    id: str

    timestamp: datetime

    source_type: Literal[
        "application_log",
        "kubernetes_event",
        "metric"
    ]

    system_id: str
    environment: str

    service_name: str | None

    namespace: str | None
    pod_name: str | None
    pod_uid: str | None

    container_name: str | None
    node_name: str | None

    severity: str | None

    event_type: str | None
    event_category: str | None
    event_action: str | None

    title: str
    message: str | None

    metric_name: str | None
    metric_value: float | None
    metric_unit: str | None

    metadata: dict
```

The exact implementation can be adjusted to match the existing project's models.

---

# 10. Unified Timeline

After normalization, all evidence is sorted by timestamp.

Example:

```text
10:05:01  Application Log
          ERROR - Database connection timeout

10:05:05  Metric
          CPU = 45%

10:05:08  Kubernetes Event
          BackOff

10:05:10  Metric
          CPU = 92%

10:05:12  Application Log
          ERROR - Payment request failed

10:05:15  Metric
          Restart count +1
```

The timeline provides the basic temporal context for the future Analysis Agent.

---

# 11. Time-Based Correlation

Events should not require identical timestamps to be considered related.

For example:

```text
Application Error
10:05:01

Kubernetes BackOff
10:05:08
```

The difference is:

```text
7 seconds
```

If the configured correlation window is 30 seconds, these records are temporally related.

Configuration:

```text
CORRELATION_TIME_WINDOW_SECONDS=30
```

The engine can then represent:

```json
{
  "relationship_type": "temporal",
  "time_delta_seconds": 7
}
```

This does not imply causation.

It only indicates temporal proximity.

---

# 12. Infrastructure Correlation

Time alone is insufficient.

For example:

```text
10:05:01 payment-api ERROR

10:05:03 inventory-api ERROR
```

These events are close in time but may be unrelated.

Therefore, infrastructure identity is also considered.

Relevant fields include:

```text
System
Environment
Service
Namespace
Pod
Container
Node
```

Correlation strength generally follows:

```text
Same container
      ↓
Same pod
      ↓
Same service
      ↓
Same namespace
      ↓
Same node
      ↓
Same system/environment
      ↓
Temporal relationship only
```

This allows the engine to distinguish related incidents from unrelated simultaneous activity.

---

# 13. Correlation Scoring

The engine can calculate a deterministic correlation score.

An initial scoring model can be:

```text
Same container       +5
Same pod             +5
Same service         +4
Same namespace       +2
Same node            +1

Within 5 seconds     +5
Within 15 seconds    +3
Within 30 seconds    +1
```

The implementation should avoid double-counting identical relationships.

The score should also include reasons.

Example:

```json
{
  "score": 12,
  "reasons": [
    "same_pod",
    "same_service",
    "within_15_seconds"
  ]
}
```

The score is used to organize evidence.

It is **not a probability of causation**.

---

# 14. Evidence Relationships

Relationships can be represented as:

```python
class EvidenceRelationship(BaseModel):

    source_id: str
    target_id: str

    relationship_type: Literal[
        "temporal",
        "infrastructure",
        "metric_change"
    ]

    score: float

    time_delta_seconds: float

    reasons: list[str]
```

Example:

```text
Application ERROR
       │
       │ same pod
       │ same service
       │ 7 seconds apart
       ▼
Kubernetes BackOff
```

---

# 15. Metric Correlation

Metrics require additional processing because they are time series.

For example:

```text
CPU

10:04:30 → 40%
10:04:40 → 42%
10:04:50 → 41%
10:05:00 → 43%
10:05:10 → 92%
10:05:20 → 95%
```

The engine can calculate:

```text
Average
Minimum
Maximum
Baseline
Peak
Change
```

Example:

```text
Baseline = 42%
Peak     = 95%
Increase = 53 percentage points
```

The engine can generate:

```json
{
  "type": "CPU_SPIKE",
  "baseline": 42,
  "peak": 95,
  "increase": 53
}
```

It must not conclude:

```text
CPU caused the payment failure.
```

That conclusion belongs to the Analysis Agent.

---

# 16. Deterministic Operational Signals

The Correlation Engine can detect predefined signals.

Initial signals include:

```text
ERROR_BURST
HTTP_5XX_BURST
KUBERNETES_BACKOFF
KUBERNETES_OOM
POD_RESTART
POD_NOT_READY
CPU_SPIKE
MEMORY_SPIKE
```

These signals summarize important observations.

They are not root-cause conclusions.

---

# 17. Error Burst Detection

An error burst occurs when many application errors appear within a defined time period.

Example:

```text
10:05:01 ERROR
10:05:03 ERROR
10:05:05 ERROR
10:05:08 ERROR
10:05:10 ERROR
```

Configuration:

```text
ERROR_BURST_THRESHOLD=5
ERROR_BURST_WINDOW_SECONDS=60
```

The engine generates:

```json
{
  "type": "ERROR_BURST",
  "severity": "high",
  "service": "payment-api",
  "count": 5,
  "window_seconds": 10
}
```

---

# 18. HTTP 5xx Detection

Application logs may contain:

```text
http.status_code
```

The engine can detect a burst of:

```text
500
502
503
504
```

responses.

Example:

```json
{
  "type": "HTTP_5XX_BURST",
  "service": "payment-api",
  "count": 34,
  "window_seconds": 60
}
```

This does not determine why the requests failed.

---

# 19. Kubernetes Signal Detection

Kubernetes events can be normalized into signals.

Examples:

```text
BackOff
    ↓
KUBERNETES_BACKOFF

OOMKilled
    ↓
KUBERNETES_OOM

Pod restart
    ↓
POD_RESTART

Readiness failure
    ↓
POD_NOT_READY
```

Example:

```json
{
  "type": "KUBERNETES_BACKOFF",
  "pod": "payment-api-abc",
  "severity": "high"
}
```

---

# 20. Restart Detection

Restart metrics can show:

```text
2 → 3
```

or:

```text
2 → 5
```

The engine can generate:

```json
{
  "type": "POD_RESTART",
  "pod": "payment-api-abc",
  "increase": 3
}
```

This provides important evidence for the future AI analysis.

---

# 21. CPU and Memory Signals

Resource thresholds can be configured:

```text
CPU_SPIKE_PERCENT=80
MEMORY_SPIKE_PERCENT=80
```

For example:

```text
CPU = 92%
```

can generate:

```text
CPU_SPIKE
```

and:

```text
Memory = 91%
```

can generate:

```text
MEMORY_SPIKE
```

Again, these are observations, not conclusions.

---

# 22. Correlation Groups

A large investigation can contain thousands of evidence records.

The engine should group closely related evidence into investigation clusters.

Example:

```text
Correlation Group #1

10:05:01
Application ERROR
Database connection timeout

10:05:08
Kubernetes BackOff

10:05:10
CPU 92%

10:05:12
Application ERROR
Payment request failed

10:05:15
Restart count +1
```

This group can contain:

```text
start_time
end_time
evidence_ids
signals
```

The group does not contain an AI-generated root cause.

---

# 23. Correlation Group Model

Example:

```python
class CorrelationGroup(BaseModel):

    id: str

    start_time: datetime
    end_time: datetime

    evidence_ids: list[str]

    signals: list[str]

    summary: str | None
```

If a summary is included, it should be deterministic.

For example:

```text
"5 evidence records across 3 source types"
```

It should not contain an LLM-generated explanation.

---

# 24. Final Correlated Evidence

The output of the Correlation Engine is:

```python
class CorrelatedEvidence(BaseModel):

    timeline: list[TimelineEvidence]

    relationships: list[EvidenceRelationship]

    groups: list[CorrelationGroup]

    signals: list[OperationalSignal]

    statistics: dict

    investigation_window: TimeRange
```

This becomes the primary input to the next AI stage.

---

# 25. End-to-End Example

User asks:

```text
Why is payment-api failing in the last 120 minutes?
```

### Step 1 — Orchestrator

Creates:

```json
{
  "system_id": "ecommerce-platform",
  "environment": "production",
  "service": "payment-api",
  "time_range": {
    "type": "relative",
    "duration": "120m"
  },
  "required_data": [
    "application_logs",
    "kubernetes_events",
    "metrics"
  ]
}
```

### Step 2 — Dispatcher

Executes:

```text
ApplicationLogTool
KubernetesEventTool
MetricsTool
```

### Step 3 — Evidence

Returns:

```text
Application Logs: 347
Kubernetes Events: 21
Metric Series: 8
```

### Step 4 — Correlation Engine

Creates:

```text
Unified Timeline
      +
Relationships
      +
Correlation Groups
      +
Operational Signals
```

### Step 5 — Output

Example:

```text
Detected Signals:

HIGH
KUBERNETES_BACKOFF

HIGH
ERROR_BURST

HIGH
POD_RESTART

MEDIUM
CPU_SPIKE
```

Timeline:

```text
10:05:01 ERROR
10:05:08 BackOff
10:05:10 CPU 92%
10:05:12 ERROR
10:05:15 Restart +1
```

At this point the system has **organized the evidence but has not decided the cause**.

---

# 26. Why the Correlation Engine Is Important

Without correlation:

```text
Qwen receives:

347 logs
21 events
8 metric series
```

The LLM must perform:

```text
timestamp analysis
sorting
grouping
metric calculations
relationship detection
infrastructure matching
```

This wastes context and increases the possibility of reasoning errors.

With correlation:

```text
347 logs
21 events
8 metric series
        │
        ▼
Correlation Engine
        │
        ▼
Relevant timeline
Relationships
Signals
Groups
Statistics
        │
        ▼
Qwen
```

The LLM can focus on what it does best:

> **Reasoning over structured evidence and explaining the likely cause.**

---

# 27. Separation of Responsibilities

The architecture deliberately separates responsibilities.

| Component | Responsibility |
|---|---|
| Orchestrator | Understand user question and create investigation plan |
| Log Tool | Retrieve application logs |
| Event Tool | Retrieve Kubernetes events |
| Metrics Tool | Retrieve metrics |
| Dispatcher | Execute required evidence tools |
| Correlation Engine | Correlate and organize evidence |
| Analysis Agent | Interpret evidence |
| RCA Agent | Perform deeper root-cause investigation |
| Response Agent | Explain results to user |

The Correlation Engine should not perform responsibilities belonging to the Analysis Agent.

---

# 28. Error Handling

If one evidence source fails, the correlation process should continue when possible.

Example:

```text
Application Logs      SUCCESS
Kubernetes Events     SUCCESS
Metrics               FAILED
```

The engine should still generate:

```text
Timeline
Relationships
Signals
```

from the available evidence.

The final output should indicate:

```text
Metrics evidence unavailable
```

rather than silently ignoring the failure.

---

# 29. Performance Considerations

A production system may contain thousands or millions of records.

Avoid naive:

```text
for every evidence A:
    compare with every evidence B
```

because this creates O(n²) behavior.

Instead, use:

```text
timestamp buckets
service grouping
namespace grouping
pod grouping
```

to reduce unnecessary comparisons.

For example:

```text
Evidence
   │
   ├── payment-api
   │      ├── pod-A
   │      └── pod-B
   │
   ├── order-api
   │
   └── inventory-api
```

Then perform correlation primarily within relevant groups.

---

# 30. Security and Reliability

The Correlation Engine should:

- never execute arbitrary queries
- never execute shell commands
- never modify infrastructure
- never restart pods
- never modify OpenSearch
- never modify Prometheus
- never make remediation decisions
- never expose secrets
- never claim certainty about causation

It is strictly an evidence-processing component.

---

# 31. Phase 3 Output

At the end of Phase 3, the system should produce:

```text
InvestigationPlan
        │
        ▼
InvestigationEvidence
        │
        ▼
CorrelatedEvidence
```

with:

```text
├── Chronological Timeline
├── Evidence Relationships
├── Correlation Groups
├── Operational Signals
├── Metric Statistics
└── Investigation Time Window
```

---

# 32. Next Phase

Once Phase 3 is stable, the next component is the:

## Analysis Agent

Architecture will become:

```text
User
  │
  ▼
Orchestrator
  │
  ▼
InvestigationPlan
  │
  ▼
Evidence Tools
  │
  ▼
InvestigationEvidence
  │
  ▼
Correlation Engine
  │
  ▼
CorrelatedEvidence
  │
  ▼
Analysis Agent
  │
  ▼
Qwen 2.5 Coder
  │
  ▼
Investigation Analysis
```

The Analysis Agent will finally introduce the LLM into the investigation process.

It will receive the structured evidence produced by the Correlation Engine and reason about:

- likely causes
- contributing factors
- incident sequence
- supporting evidence
- conflicting evidence
- confidence
- recommended next investigation steps

The important architectural boundary is:

```text
Correlation Engine
        =
"What happened around the same time and what evidence is related?"

Analysis Agent
        =
"What does this evidence mean and what is the likely cause?"
```

Keeping these responsibilities separate makes the overall AI log analysis system more reliable, explainable, testable, and easier to extend.