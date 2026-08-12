# Data contracts

One rule governs this whole document:

> **The application emits the final schema. Nothing renames a field anywhere
> along the path.**

Fluent Bit attaches Kubernetes identity on top and changes nothing else. There is
no mapping layer between the producer and the query, so there is nothing that can
silently drift out of sync — the class of bug where an app writes `http_status`,
a collector looks for `http_status_code`, and the field simply never exists in
the index.

---

## Application logs — `logintel-logs-*`

Emitted by `testbed/apps/service.py`, one JSON object per line.

```json
{
  "@timestamp": "2026-08-09T15:15:57.972Z",
  "system":      { "id": "shopdemo", "name": "Shop Demo" },
  "environment": "staging",
  "service":     { "name": "payment-api", "version": "1.0.0", "tier": "backend" },
  "kubernetes": {
    "cluster":   "logintel-testbed",
    "namespace": "shopdemo",
    "pod":       { "name": "payment-api-69d7b68776-mqxxd", "uid": "58d5..." },
    "container": { "name": "app", "image": "docker.io/library/python:3.11-slim" },
    "node":      { "name": "logintel" }
  },
  "source":     { "type": "kubernetes", "collector": "fluent-bit" },
  "log":        { "level": "ERROR", "message": "Upstream dependency payment-db failed: DependencyTimeout" },
  "event":      { "category": "http", "action": "payment-api.request", "outcome": "failure" },
  "http":       { "method": "POST", "route": "/api/payment", "status_code": 502, "response_time_ms": 2013.4 },
  "error":      { "type": "DependencyTimeout", "message": "..." },
  "dependency": { "name": "payment-db", "outcome": "failure", "duration_ms": 2001.2 },
  "trace":      { "id": "tr-9f2ab1c3d4e5" },
  "request":    { "id": "rq-11223344aabb" }
}
```

| Field | Set by | Used for |
|---|---|---|
| `system.id`, `environment` | pod labels via Fluent Bit Lua | scoping every query |
| `service.name` | the app itself, falling back to the `app` label | per-service signals |
| `log.level` | the app | error rate, onset detection |
| `log.message` | the app | pattern fingerprinting |
| `http.status_code` | the app | 5xx detection |
| `http.response_time_ms` | the app | latency |
| `error.type` | the app | error classification |
| `dependency.*` | the app | dependency attribution |
| `parse.failed` | Fluent Bit Lua | excluding unparsed noise from patterns |

**Unparsed lines.** Anything Fluent Bit cannot parse as JSON is kept, but wrapped
as `log.level: "UNKNOWN"` with `parse.failed: true`. It is counted and reported,
and excluded from pattern analysis — noise should be visible, not silently
treated as evidence.

**Stack traces.** The `multiline` filter rejoins Python/Go/Java traces before
anything else sees them, and the application catches its own exceptions and
renders them into a single record with `error.stack_trace` as one string. A
traceback must never become forty documents; it drowns every aggregation it
touches.

---

## Kubernetes events — `logintel-events-*`

Emitted by `testbed/apps/event_collector.py`.

```json
{
  "@timestamp":  "2026-08-09T15:12:28Z",
  "system":      { "id": "shopdemo", "name": "Shop Demo" },
  "environment": "staging",
  "kubernetes":  { "cluster": "logintel-testbed", "namespace": "shopdemo",
                   "pod": { "name": "payment-api-69d7b68776-mqxxd" },
                   "container": { "name": "app" }, "node": { "name": "logintel" } },
  "service":     { "name": "payment-api" },
  "event": {
    "id":              "bf92162b-dfa2-442f-a8ba-7cd72416f279",
    "kind":            "kubernetes_event",
    "type":            "Warning",
    "reason":          "Unhealthy",
    "message":         "Readiness probe failed: ...",
    "severity":        "warning",
    "count":           510,
    "first_timestamp": "2026-08-09T08:57:52Z",
    "last_timestamp":  "2026-08-09T15:12:28Z"
  },
  "involved_object": { "kind": "Pod", "name": "payment-api-69d7b68776-mqxxd" }
}
```

Three fields carry the weight:

**`event.type`** holds the real Kubernetes value, `Normal` or `Warning`. It is
tempting to reuse this field for the source system; doing so makes every event
look benign, because nothing is ever `Warning` any more.

**`event.reason`** is the primary key for signal detection. `category` and
`action` are optional enrichment and are omitted entirely when unknown, so an
`"unknown"` placeholder can never shadow a reason the detector would have
matched. A fixed reason→action table will always be incomplete, and the reasons
it misses (`Unhealthy`, `FailedMount`, `NodeNotReady`) are the interesting ones.

**`event.count` / `first_timestamp` / `last_timestamp`** are all preserved. A
single event document can represent hundreds of occurrences over hours.
`@timestamp` is the *last* firing; `first_timestamp` is when the condition began.
Ordering on the wrong one places a long-running problem after its own effects and
inverts every causal conclusion downstream.

---

## Metrics — Prometheus

Metrics stay in Prometheus. They are not copied into OpenSearch: `rate()`,
`histogram_quantile()` and range queries are the whole reason to have a TSDB, and
running two metric pipelines guarantees they will disagree eventually.

| Source | Provides |
|---|---|
| cAdvisor (via kubelet) | container CPU, memory, CFS throttling, limits |
| kube-state-metrics | restarts, ready condition, phase, last terminated reason, generations |
| application pods | `http_requests_total`, `http_request_duration_seconds`, `app_dependency_requests_total` |

The application metrics are what make RED signals possible — request rate, error
ratio, latency percentiles and dependency health. A pipeline without them can see
that a pod restarted but not that 60% of requests are failing.

Two scrape-config details are load-bearing:

**`honor_labels: true` on the kube-state-metrics job.** Without it, Prometheus
renames KSM's own `namespace`, `pod` and `container` labels to `exported_*` and
substitutes the *target's* labels. Every namespace filter against those metrics
then silently matches nothing.

**`condition="true"` on `kube_pod_status_ready`.** That metric emits one series
per condition value. Without the selector, a value of `1` could mean "ready" or
"definitely not ready", and the evidence is not interpretable.

---

## Index templates

Applied by the agent at startup, and by `scripts/bootstrap-agent.sh` before the
testbed ever writes a document (`app/sources/opensearch.py`).

Explicit mappings, not dynamic ones. Dynamic mapping makes every string a `text`
field with a `.keyword` sub-field: it doubles the index size and leaves every
query author guessing which form to use. Here `system.id` is a `keyword` and is
queried as `system.id`. Only `log.message` and `event.message` are `text`, each
with a `.keyword` sub-field for the pattern aggregation.

Ordering matters. If OpenSearch creates an index dynamically before the template
exists, `system.id` becomes `text` and every term filter matches nothing. That
failure is invisible at query time — it looks exactly like a healthy system with
no logs — so `/api/health` checks the live mappings and reports the fix.

---

## Identity labels

Pods opt into collection and carry their identity through labels:

```yaml
labels:
  app: payment-api                    # service name fallback
  logintel/log: "true"                # opt in to log collection
  logintel/system-id: shopdemo
  logintel/system-name: Shop_Demo     # underscores; label values cannot hold spaces
  logintel/environment: staging
```

Only pods with `logintel/log: "true"` are shipped. Cluster infrastructure noise is
not evidence about anyone's system, and excluding it at the source is cheaper
than filtering it at query time.
