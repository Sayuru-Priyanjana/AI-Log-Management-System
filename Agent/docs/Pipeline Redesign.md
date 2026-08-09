# AI Log Analysis System — Pipeline Diagnosis & Redesign

Status: proposal
Scope: `Agent/` (backend pipeline), with required fixes in `fluent-bit-k3s-test/`, `kubernetes-event-collector/`, `metrics-collector/`
Verified against the live stack on 2026-08-09 (OpenSearch `172.23.84.199:9200`, Prometheus `192.168.56.10:30090`, Ollama on the Windows host).

---

## Part 1 — Why the current pipeline is inaccurate

Every item below was verified against live data, not inferred from the code alone.

### 1.1 The LLM never sees the evidence (critical)

`OllamaProvider.generate()` sends no `options`. Ollama's default `num_ctx` is **2048 tokens**. Measured against the running Ollama:

```
prompt sent      : 52,103 characters (~13,000 tokens)
prompt_eval_count: 2,050 tokens
```

Ollama silently truncated ~95% of the prompt and kept only the **tail**. A marker placed at the head of the prompt was gone; the model answered from the last ~2,000 tokens.

For `AnalysisAgent`, the tail of the prompt is the schema reminder plus the last few timeline lines. The investigation context, signals, relationships and metric statistics are all discarded before the model ever reads them. This alone explains most of the incoherent output.

**Fix:** pass `options: {"num_ctx": 16384, "temperature": 0, "top_p": 0.9, "seed": 42}` and log `prompt_eval_count` on every call. If `prompt_eval_count` is within a few tokens of `num_ctx`, the context was truncated — treat that as a hard error, not a warning.

### 1.2 Retrieval returns the wrong 500 documents (critical)

`ApplicationLogTool` sorts `@timestamp: asc` and `OpenSearchClient` caps at `OPENSEARCH_MAX_RESULTS=500`.

Live: **4,668 application logs in the last 30 minutes.** The tool returns the oldest 500 — roughly the first 3 minutes of a 30-minute window. An incident that starts at minute 25 is invisible to the entire pipeline.

Raw-document fetching is the wrong primitive here regardless of sort order. Log analysis must be **aggregation-first**: count, bucket, fingerprint, then sample.

### 1.3 Kubernetes event fields are read from paths that do not exist

The collector writes (verified document):

```json
{ "event": { "reason": "Unhealthy", "message": "Readiness probe failed: ...",
             "type": "kubernetes", "action": "unknown", "severity": "unknown",
             "count": 510, "first_timestamp": "...", "last_timestamp": "..." } }
```

`KubernetesEventTool` reads:

| Reads | Actual path | Result |
|---|---|---|
| `_source.message` | `_source.event.message` | **every event message is `""`** |
| `_source.event.source.component` | does not exist | always `None` |
| `_source.event.type` | exists but is the literal `"kubernetes"` | never `Warning`/`Normal` |

Downstream consequences:

- `EvidenceNormalizer.normalize_event` sets `severity = "HIGH" if event_type == "Warning" else "INFO"`. `event_type` is always `"kubernetes"`, so **every Kubernetes event is normalized to INFO**.
- `event.count`, `event.first_timestamp`, `event.last_timestamp`, `event.severity` are dropped entirely. One live document represents **510 occurrences spanning 6h 15m**, and the timeline treats it as a single point event at `last_timestamp`.

### 1.4 `KUBERNETES_BACKOFF` and `KUBERNETES_OOM` can never fire

`SignalDetector._detect_kubernetes_signals` matches substrings `"backoff"` / `"oom"` against `event_action`.

- `mappings.normalize_reason` maps `BackOff → action "restart"` and `OOMKilled → action "kill"`. Neither string contains `backoff` or `oom`.
- `normalize_event` sets `event_action = event.action or event.reason`. Since `action` is `"unknown"` (truthy) for every unmapped reason, the reason never reaches `event_action`.
- The mapping table has 15 entries. Live event reasons include `Unhealthy` (18, the most frequent) and `FailedMount` — both unmapped, both → `unknown`.

These two signals are dead code in every possible run.

### 1.5 Metric thresholds compare raw values against percentages

`_detect_metric_spikes` compares against `CPU_SPIKE_PERCENT=80.0` / `MEMORY_SPIKE_PERCENT=80.0`, but the metrics are absolute:

| Metric | PromQL result | Threshold | Behaviour |
|---|---|---|---|
| `pod_cpu_usage` | `rate(container_cpu_usage_seconds_total[5m])` → cores, e.g. `0.42` | `> 80.0` | **never fires** |
| `pod_memory_usage` | `container_memory_working_set_bytes` → e.g. `45,944,832` | `> 80.0` | **always fires** |

Both signals are wrong in opposite directions. `baseline` is also the mean of the whole window *including* the spike, so `increase` understates the change.

### 1.6 `kube_pod_status_ready` is read ambiguously

Live: 51 series, each carrying a `condition` label (`true`/`false`/`unknown`). The tool ignores `condition`, so a returned value of `1` may mean "ready" or "explicitly not ready". The evidence handed to the model is not interpretable.

`kube_pod_container_status_restarts_total` is scraped through the `kubernetes-service-endpoints` job, which sets `namespace=monitoring` as a target label; kube-state-metrics' own namespace is demoted to `exported_namespace`. Any future namespace filter on that metric will silently match nothing.

### 1.7 Application logs are missing the fields the design depends on

The app emits `http_status` and `duration_ms`; `normalize.lua` looks for `http_status_code` and `response_time_ms`. The `http` object is therefore **absent from every document** — verified.

| Design expects | Emitted by app | Mapped by Lua | Read by tool | In OpenSearch |
|---|---|---|---|---|
| `http.status_code` | `http_status` | `http_status_code` | not read | **missing** |
| `http.response_time_ms` | `duration_ms` | `response_time_ms` | not read | **missing** |
| `request.id` | — | `request.id` | `http.request.id` | mismatch |
| `error.type` / `error.code` | — | `error_type`/`error_code` | not read | missing |
| `event.category/action/outcome` | only `event_type` | — | read | missing |

`HTTP_5XX_BURST` is configured in `correlation/config.py` but has no detector, and could not work anyway.

### 1.8 Stack traces flood the index as individual documents

Live level breakdown over 6h: `ERROR 16,555 / UNKNOWN 9,849 / INFO 2,236`. The 9,849 `UNKNOWN` documents are Python tracebacks split one line per document (469 identical `self.handle()` documents, 938 `------` separators). Fluent Bit has no multiline parser configured. These dominate the timeline and distort burst detection.

### 1.9 Correlation output is noise

- **`normalize_metrics` emits one `TimelineEvidence` per metric sample.** 4 templates × N series × 30 samples per 30-minute window produces hundreds of "observation" points interleaved with log lines. Time series do not belong on an event timeline.
- **`find_relationships` is O(n²) over a 30-second window.** 500 same-pod log lines produce thousands of edges that all score exactly `10.0` (`same_pod` + `within_5_seconds`). "Top 20 by score" therefore hands the model 20 indistinguishable log→log pairs carrying zero information.
- **`_build_correlation_groups` returns exactly one group containing every evidence ID.** It is not clustering.
- **`datetime.fromtimestamp(float(val[0]))`** in `MetricsTool` produces a naive local-time datetime while every other timestamp in the system is UTC-aware. Metric samples are placed on the timeline offset by the WSL timezone.

### 1.10 The one thing correlation computes well is never sent to the model

`build_analysis_context` prints `--- METRIC STATISTICS ---` from `evidence.statistics`, which contains only counts (`total_logs`, `total_events`, …). The computed `MetricSummary` (average / maximum / minimum / initial / final / increase) **never reaches the LLM**.

Worse, the timeline is de-duplicated on the key `(source_type, title, message)`. Metric entries have `message = None` and a constant title, so all 30 samples collapse into one line showing only the **first** sample's value. The model sees `pod_cpu_usage = 0.03` and no trend, no peak, no direction.

### 1.11 Evidence IDs are unverifiable

`uuid4()` IDs are generated fresh on every run and never persisted. The prompt instructs the model to cite `evidence_ids` for every conclusion, but nothing checks the citations, the UI cannot resolve them, and a hallucinated ID is indistinguishable from a real one.

### 1.12 The Orchestrator hallucinates service names

The LLM is asked to produce `service` with no knowledge of which services exist in the selected system. That value goes straight into a `term` filter on `service.name.keyword`. A near-miss (`payment` vs `payment-api`) yields zero hits, and the pipeline reports "no incident detected" with high confidence.

### 1.13 Structural gaps

- **No baseline.** Nothing is ever compared against a healthy period, so "is this abnormal?" is unanswerable.
- **No verification.** Nothing checks the model's claims against the evidence it was given.
- **No persistence.** `/investigations/run` streams NDJSON and discards everything. There is no run history, so there is no way to measure whether a change improved accuracy.
- **Two competing metric pipelines.** `metrics-collector` writes 22,311 documents to `metrics-kubernetes-*` in OpenSearch; `MetricsTool` queries Prometheus instead. One is dead weight.
- **`environment` is hardcoded to `"production"` in `normalize.lua`** while the app defaults `ENVIRONMENT=test`. Selecting `test` in the UI returns zero results from a healthy cluster.
- **Errors are returned as HTTP 200.** A failure mid-stream yields `{"step":"error"}` inside a 200 response.

---

## Part 2 — Design principle

> With a 7B local model, accuracy comes from **retrieval and deterministic feature extraction**, not from prompting.

Anything that can be measured must be measured in Python. The LLM's only jobs are:

1. mapping a natural-language question onto a constrained plan, and
2. selecting among pre-computed candidate explanations and writing the explanation in words.

The model must never invent a cause that no deterministic signal supports.

---

## Part 3 — Target pipeline

```
User question + selected system
        │
[0] System Registry ──────────── deterministic (cached OpenSearch aggregations)
        │  known services, namespaces, pods, environments
        ▼
[1] Orchestrator Agent ───────── LLM (narrow: intent + service from a supplied list)
        │  InvestigationPlan
        ▼
[2] Incident Window Detector ─── deterministic
        │  narrows "last 30m" to the actual anomaly window + a baseline window
        ▼
[3] Evidence Tools ───────────── deterministic, aggregation-first, parallel
        │  logs / k8s events / metrics — for BOTH windows
        ▼
[4] Feature & Signal Engine ──── deterministic, unit-aware, baseline-relative
        │  typed OperationalSignal[] with magnitudes
        ▼
[5] Hypothesis Engine ────────── deterministic rules
        │  ranked CandidateCause[] with supporting/contradicting signals
        ▼
      ┌─ confidence low or top-2 too close ─→ back to [3] with a narrowed scope (max 2 extra rounds)
        ▼
[6] Analysis Agent ───────────── LLM (select + explain, constrained)
        ▼
[7] Verifier ────────────────── deterministic (citation allowlist, causality ordering)
        ▼
[8] Persist + Respond ────────── investigations-* index, then Response Agent (LLM, optional)
```

### Step 0 — System Registry (new, deterministic)

Builds and caches, per `system_id`:

- services (`terms` on `service.name.keyword`)
- namespaces, pod name prefixes
- environments actually present
- Prometheus label selectors for the system

Source: OpenSearch aggregations, refreshed every few minutes, optionally overlaid with static config. Kills service-name hallucination (§1.12) — the Orchestrator picks from an enumerated list and is rejected if it picks anything else.

New endpoint: `GET /api/systems` — replaces the mocked list in the UI.

### Step 1 — Orchestrator Agent (LLM, narrowed)

Keep the LLM only for `intent` + `service` + `duration`. Everything else is deterministic:

- `system_id` / `environment` come from the request, never the model.
- `service` must be one of the registry's services, or `null`. Validate, do not trust.
- Absolute timestamps are never accepted from the model — only a relative duration from a small allowed set (`15m`, `30m`, `1h`, `6h`, `24h`, `7d`).
- `required_data` is derived from `intent` by a lookup table, not generated.

### Step 2 — Incident Window Detector (new, deterministic)

The single biggest accuracy win. Before fetching anything:

1. `date_histogram` (1-minute buckets) of error-level log counts across the requested range.
2. Find the onset — first bucket exceeding `median + k·MAD` of the preceding buckets.
3. Produce `incident_window` (onset − 2 min → onset + N) and `baseline_window` (a quiet stretch before onset, or the same clock window on the previous day).

Everything downstream is fetched for **both** windows. "Is this abnormal?" becomes answerable.

### Step 3 — Evidence tools (deterministic, aggregation-first)

Replace the three current tools with a tool set whose outputs are bounded by construction.

| Tool | Returns | Backend |
|---|---|---|
| `log_histogram` | per-minute counts by level and service | OpenSearch agg |
| `log_pattern_summary` | top N message fingerprints (numbers/UUIDs/IPs/hex masked) with count, first_seen, last_seen, level, one example `_id` | OpenSearch agg |
| `log_samples` | ≤3 raw documents per top pattern, plus ≤10 around onset | OpenSearch |
| `error_first_occurrence` | earliest occurrence per pattern — answers "what happened first" | OpenSearch agg |
| `k8s_events` | `reason`, `event.message`, `count`, `first_timestamp`, `last_timestamp`, severity from the real event type | OpenSearch |
| `pod_lifecycle` | restart deltas, ready-condition transitions, phase changes, `last_terminated_reason` | kube-state-metrics |
| `resource_usage` | CPU **as a fraction of limit**, memory **as a fraction of limit**, CFS throttling ratio — baseline vs incident summaries, not raw samples | Prometheus |
| `traffic_health` | request rate, 5xx ratio, latency p50/p95/p99 | Prometheus (`http_requests_total`, `http_request_duration_seconds` — already exported and currently unused) |
| `dependency_health` | `up{}` for dependencies + their error patterns | Prometheus + OpenSearch |
| `deployment_change` | did a Deployment/ReplicaSet change in the window | kube-state-metrics + k8s events |

Rules for every tool:

- Queries are Python templates. The LLM never writes PromQL or OpenSearch DSL. (This is already the intent — keep it.)
- Every query is scoped by `system_id`, `environment`, and namespace from the registry.
- Metrics return **summaries plus a downsampled series** (≤20 points), never a point per sample.
- All timestamps UTC-aware. Fix `datetime.fromtimestamp` → `datetime.fromtimestamp(ts, tz=timezone.utc)`.
- `traffic_health` and `dependency_health` are the two highest-value additions — the current pipeline has no notion of request rate, error rate, latency, or dependency availability at all.

### Step 4 — Feature & Signal Engine (rewrite of `SignalDetector`)

Every signal is baseline-relative and unit-aware. Never compare a raw value to a percentage constant.

| Signal | Trigger |
|---|---|
| `ERROR_RATE_SPIKE` | incident error rate ≥ 3× baseline **and** ≥ N/min absolute floor |
| `NEW_ERROR_PATTERN` | fingerprint absent in baseline, present in incident — cheap and very high value |
| `HTTP_5XX_BURST` | 5xx ratio from Prometheus counters (logs lack status codes until §1.7 is fixed) |
| `LATENCY_DEGRADATION` | p95 incident ≥ 2× p95 baseline |
| `POD_RESTART` / `CRASHLOOP` | restart counter delta + `BackOff` reason |
| `OOM_KILL` | `OOMKilling` event or `last_terminated_reason="OOMKilled"` |
| `READINESS_FAILURE` | `Unhealthy` events + `kube_pod_status_ready{condition="true"} == 0` |
| `SCHEDULING_FAILURE` | `FailedScheduling` |
| `CPU_SATURATION` | `usage / limit ≥ 0.9` |
| `CPU_THROTTLING` | `rate(container_cpu_cfs_throttled_seconds_total) / rate(container_cpu_cfs_periods_total) ≥ 0.25` |
| `MEMORY_PRESSURE` | `working_set / limit ≥ 0.9` |
| `DEPENDENCY_UNAVAILABLE` | `up == 0`, or connection-refused/timeout patterns naming a dependency |
| `DEPLOYMENT_CHANGE` | generation/replicaset change within the window |

Each signal carries `{type, severity, service, pod, first_seen, last_seen, magnitude:{baseline, incident, unit}, evidence_refs[]}`.

`first_seen` is what makes causal ordering possible in step 5, so it must be accurate — which is why §1.3 (`event.count` / `first_timestamp`) has to be fixed.

Drop from the timeline: per-sample metric points, and the pairwise relationship graph. Replace relationships with a small number of **meaningful** links only — same pod *and* different source type *and* within the window (log→event, event→metric). Cap at ~30, deduplicate by `(source_type_pair, pod)`.

### Step 5 — Hypothesis Engine (new, deterministic)

This is the "logic" that is currently missing. Encode a small rule set over signals; each rule emits a candidate:

```
CandidateCause {
  id, hypothesis, category,
  supporting_signals[], contradicting_signals[],
  onset, score
}
```

Starter rules:

| Rule | Candidate |
|---|---|
| `DEPENDENCY_UNAVAILABLE` precedes `ERROR_RATE_SPIKE`, error pattern names the dependency | downstream dependency failure |
| `MEMORY_PRESSURE` → `OOM_KILL` → `POD_RESTART` | memory limit exceeded |
| `CRASHLOOP` with a FATAL log at container start | startup / configuration failure |
| `CPU_SATURATION` + `READINESS_FAILURE`, no restarts | saturation-induced probe timeout |
| `DEPLOYMENT_CHANGE` immediately precedes onset | change-induced regression |
| `SCHEDULING_FAILURE` with no running pods | capacity / scheduling constraint |
| `ERROR_RATE_SPIKE` with no infrastructure signal | application-level fault |

Ranking is by **causal ordering, not just score**: a candidate whose onset precedes the symptom onset outranks one that follows it. Contradicting signals subtract.

This makes root-cause analysis reproducible and explainable, and bounds what the model is allowed to conclude.

### Step 6 — Analysis Agent (LLM, narrowed and split)

Input becomes small and dense: incident window, ordered signals with magnitudes, ranked candidates, ≤15 representative log lines, ≤10 events, metric summaries. Target ≤4,000 tokens.

Split the single wide call into two narrow ones — small models degrade badly when asked for ten reasoning fields at once:

- **6a. Narrative** — write the incident timeline in prose. No judgment, no cause.
- **6b. Selection** — choose a `candidate_id` from the supplied list (or `none`), assign confidence, cite `evidence_ids` from a supplied allowlist, and list what to check next.

LLM call requirements:

- `options: {num_ctx: 16384, temperature: 0, seed: 42}` on every call.
- Use Ollama's structured output: pass the Pydantic JSON schema as `format`, not the string `"json"`.
- Log `prompt_eval_count` and fail loudly on truncation.
- Consider testing `qwen2.5:7b-instruct` against `qwen2.5-coder` for step 6 — coder models are tuned for code, and no query generation happens here by design. Cheap to A/B once step 8 exists.

### Step 7 — Verifier (new, deterministic)

- Any `evidence_id` not in the allowlist → strip and flag.
- A cause whose supporting signals are absent → reject, fall back to the top deterministic candidate.
- Claimed cause onset must precede symptom onset → otherwise downgrade confidence and record why.
- If the model's choice disagrees with the deterministic ranking, surface both. Disagreement is information, not something to hide.

### Step 8 — Persist + evaluate

Write every run to `investigations-*`: plan, windows, signals, candidates, exact prompt, raw model output, verified result, latency.

Then build the evaluation harness on top of the ten scripts in `incident-test/incidents/`. Each script has a known ground-truth cause:

```
01-cpu-saturation      → CPU_SATURATION
02-oomkill             → OOM_KILL
03-crashloop           → CRASHLOOP / startup failure
04-readiness-failure   → READINESS_FAILURE
05-http500-burst       → HTTP_5XX_BURST
06-dependency-failure  → DEPENDENCY_UNAVAILABLE (payment-db)
07-pod-restart         → POD_RESTART
08-scheduling-failure  → SCHEDULING_FAILURE
09-network-dependency  → DEPENDENCY_UNAVAILABLE
10-high-load           → LATENCY_DEGRADATION / CPU_SATURATION
```

Score two things separately: **signal recall** (did the deterministic engine detect the right signal?) and **cause accuracy** (did the final answer name the right cause?). Signal recall is the leading indicator — if it is low, no amount of prompt work will help.

Without this harness there is no way to know whether any change actually improved accuracy.

### Optional — iterative deepening

If step 5 produces no candidate above threshold, or the top two are within ~15%, re-enter step 3 with a narrowed scope (the dependency's own logs, a wider window, sibling pods). Cap at 2 extra rounds. This is the point where a multi-agent loop earns its cost; a fixed one-shot chain does not need one.

---

## Part 4 — Required fixes outside `Agent/`

**Fluent Bit (`fluent-bit-k3s-test/`)**
- Add a multiline parser so Python tracebacks become one document, not 469 (§1.8).
- Align field names end to end: either the app emits `http_status_code`/`response_time_ms`, or `normalize.lua` reads `http_status`/`duration_ms` (§1.7).
- Stop hardcoding `environment = "production"`; read the `ai-log/environment` pod label with a configurable fallback.
- Emit `request.id` consistently, and make `ApplicationLogTool` read the same path.

**Event collector (`kubernetes-event-collector/`)**
- Set `event.type` from the Kubernetes event's real `type` (`Normal`/`Warning`) — it is currently overwritten with the constant `"kubernetes"` (§1.3). Move the source type to `source.type`, which already exists.
- Derive `event.severity` from that type instead of the incomplete reason table.
- Keep `reason` as the primary key for signal detection; treat `category`/`action` as optional enrichment. Expand the table (`Unhealthy`, `FailedMount`, `NodeNotReady`, `Failed`, `FailedCreatePodSandBox`, `Preempting`, `NodeHasDiskPressure`, …) but never let `"unknown"` shadow the reason.

**Metrics (`metrics-collector/`)**
- Pick one path. Recommendation: keep Prometheus (real time series, `rate()`, `histogram_quantile()`) and stop writing `metrics-kubernetes-*`, or demote it to a fallback when Prometheus is unreachable. Running both costs storage and guarantees they will drift.

**OpenSearch**
- Add explicit index templates for `logs-application-*` and `events-kubernetes-*`: `keyword` for `system.id`, `service.name`, `log.level`, `event.reason`, `kubernetes.pod.name`; `text` only for `log.message` (with a `keyword` sub-field for fingerprinting). Removes the dynamic-mapping `.keyword` guesswork and shrinks the indices.

---

## Part 5 — Sequencing

| Priority | Work | Why first |
|---|---|---|
| **P0** | `num_ctx` + `temperature` + truncation detection (§1.1); sort `desc` + aggregation-first retrieval (§1.2); fix k8s event field paths (§1.3) | The model currently cannot see the evidence. Nothing else matters until it can. Cheap, hours. |
| **P1** | Incident Window Detector + baseline window (step 2) | Largest single accuracy gain per line of code. |
| **P2** | Rewrite signals: unit-aware, baseline-relative (step 4); delete per-sample metric timeline entries and the O(n²) relationship graph (§1.9); send `MetricSummary` to the model (§1.10) | Makes the evidence handed to the model true and compact. |
| **P3** | System Registry (step 0) + Orchestrator validation (§1.12) | Stops silent zero-hit investigations. |
| **P4** | Hypothesis Engine (step 5) + Verifier (step 7) + split Analysis Agent (step 6) | Adds the missing logic and bounds the model. |
| **P5** | Persistence + evaluation harness (step 8) | Turns further work from guessing into measurement. Pull earlier if convenient. |
| **P6** | Data pipeline fixes (Part 4) | Unlocks HTTP/latency signals and clean stack traces. |
| **P7** | Iterative deepening | Only worth it once P0–P5 are stable. |

A reasonable reading of P0–P2 is: most of the "not accurate, not logical" behaviour disappears once the model can see correct, compact, baseline-relative evidence — before any prompt engineering.
