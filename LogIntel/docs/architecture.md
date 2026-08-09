# LogIntel — architecture

## The governing constraint

The model is a 7B parameter LLM running locally on CPU/consumer GPU. That fact
determines the whole design:

> Accuracy comes from **retrieval and deterministic feature extraction**, not
> from prompting.

Everything that can be measured is measured in Python, where it is reproducible,
unit-tested and inspectable. The LLM does two narrow jobs — mapping a question to
a constrained plan, and **choosing among pre-computed candidate causes** — and it
is verified afterwards on both.

The practical consequence: the system cannot assert a cause that no deterministic
signal supports. A hallucinated explanation has nowhere to enter.

---

## Pipeline

```
question + selected system
   │
   ├─[0] System Registry ────────── deterministic   what exists: services, namespaces, environments
   │
   ├─[1] Orchestrator ───────────── LLM (narrow)    intent + service + duration, all validated
   │
   ├─[2] Window Resolver ────────── deterministic   where the incident actually is, plus a baseline
   │
   ├─[3] Evidence Tools ─────────── deterministic   aggregation-first, both windows, in parallel
   │
   ├─[4] Signal Engine ──────────── deterministic   unit-aware, baseline-relative, with onsets
   │
   ├─[5] Hypothesis Engine ──────── deterministic   ranked candidate causes, ordered causally
   │
   ├─[6] Analyst ────────────────── LLM (narrow)    pick one candidate, justify, cite
   │
   ├─[7] Verifier ───────────────── deterministic   citations, support, causal order, confidence caps
   │
   └─[8] Store ──────────────────── OpenSearch      full audit trail, and the basis for evaluation
```

Two stages use an LLM. Both are constrained, both are validated, and the run
completes usefully if either fails.

---

## Why each stage exists

### [0] System Registry — `app/registry/systems.py`

Aggregates OpenSearch to discover which systems, environments, namespaces and
services actually exist. The orchestrator may only choose service names from
this list.

This closes the single largest accuracy hole in a naive design. Ask a model for
a service name with no knowledge of what exists and it will produce a plausible
one; that name becomes a term filter; the filter matches nothing; and the
investigation reports that everything is fine. The failure is invisible — it
looks identical to a healthy system.

### [1] Orchestrator — `app/agents/orchestrator.py`

The model contributes exactly three things: `intent`, `service`, `duration`.

- `system_id` and `environment` come from the request, never the model.
- `service` is resolved against the registry. An exact match wins; an
  unambiguous near-miss is accepted and noted; anything else is rejected with an
  explanation rather than silently dropped.
- `duration` must come from a fixed list. Free-form durations invite answers like
  "since last Tuesday" that no query can use.
- `tools` are derived from intent by lookup. There is no judgement there worth
  spending a token on.

If the model is unreachable or returns nonsense, keyword heuristics take over and
the plan is marked `planner: "heuristic"` so a degraded run never looks like a
confident one.

### [2] Window Resolver — `app/pipeline/windows.py`

The highest-value stage in the pipeline. "The last 30 minutes" is a request, not
an incident. This stage finds where the error rate actually departs from its own
normal level, and carves out:

- an **incident window** starting shortly before the onset, and
- a **baseline window** from a quiet stretch before it.

Three details make it work:

- **Median and MAD, not mean and standard deviation.** Error counts are spiky; a
  single burst inflates a standard deviation enough to hide the thing you are
  looking for.
- **The baseline is estimated from the quiet 60% of the range.** When an incident
  fills half the window it drags the median up with it, and a threshold derived
  from that median sits above the very spike it should catch.
- **Onsets must be sustained.** One stray error in a silent window is not an
  incident.

It also distinguishes three outcomes that are genuinely different and must not be
conflated: an onset was found; errors were *never quiet* in the whole search
range (so the incident predates everything visible); or nothing departed at all.

Without a baseline, "is this abnormal?" is unanswerable — which is why the
verifier caps confidence whenever one could not be established.

### [3] Evidence Tools — `app/tools/`

Aggregation-first. Nothing bulk-fetches raw documents.

| Tool | Returns |
|---|---|
| `LogTool.histogram` | per-minute counts by level |
| `LogTool.collect` | message **patterns** with counts, onsets and baseline counts |
| `LogTool.samples` | a few raw error lines from the *start* of the incident |
| `EventTool` | events preserving `reason`, `type`, `count`, `first_timestamp` |
| `MetricTool` | 16 PromQL templates, summarised for both windows |

Three decisions worth stating:

**Patterns, not lines.** A 30-minute window can hold tens of thousands of log
lines but only a couple of dozen distinct message templates. Messages are
fingerprinted (numbers, UUIDs, IPs, pod names, timestamps masked) so
`failed after 1203ms` and `failed after 87ms` are one pattern. Asking for "the
first 500 documents" instead returns the first three minutes and misses the
incident entirely.

**Scope is the system, not the focus service.** Filtering logs to `checkout-api`
because the question named it would hide the `payment-db` failure underneath —
and in a dependency failure, that is the answer. The focus service drives
prioritisation and reporting only.

**Everything is fetched for both windows.** Baseline comparison is not an
add-on; it is what makes a threshold meaningful.

### [4] Signal Engine — `app/pipeline/signals.py`

Turns evidence into typed signals. The invariant:

> Every threshold is a **ratio** or a **baseline-relative multiple**. None
> compares a raw value against a bare constant.

This is not pedantry. CPU is measured in cores (`0.42`) and memory in bytes
(`45,944,832`). A threshold of "80 percent" tested against those raw values never
fires for CPU and always fires for memory. Instead: CPU against its own limit,
memory against its own limit, throttled periods as a fraction of total periods.

Two other properties matter downstream:

- **Onsets are real.** A signal's `first_seen` is the first sample that actually
  crossed the threshold, and for Kubernetes events it is `first_timestamp`, not
  the document timestamp. A recurring event's document timestamp is its *most
  recent* firing — using it would place a long-running condition after everything
  it caused.
- **Reason is the key for events.** Deriving signals from a normalised `action`
  field means anything outside the mapping table becomes invisible, and in
  practice the unmapped reasons (`Unhealthy`, `FailedMount`) are the interesting
  ones.

### [5] Hypothesis Engine — `app/pipeline/hypotheses.py`

Ten rules over the signal set, each producing a candidate with supporting *and*
contradicting signals. Ranking is causal:

- a candidate whose onset precedes the first symptom gains score;
- one that starts after the symptoms loses it, because an explanation that
  begins after the thing it explains is not an explanation;
- the earliest-onset candidate gains a further bonus.

This is where "the database went down, and the two services above it reporting
errors are symptoms" gets decided — in Python, reproducibly.

### [6] Analyst — `app/agents/analyst.py`

Two separate calls, deliberately:

- **selection** — choose one `candidate_id`, give a confidence, justify briefly,
  cite evidence IDs.
- **narration** — write the summary paragraph from an already-fixed conclusion.

Asking a 7B model for a choice, a confidence, a timeline, a rationale and next
steps in one response degrades all of them. A constrained choice is something it
does well; open-ended root-cause analysis over raw evidence is not.

The timeline is **not** generated by the model (`app/pipeline/timeline.py`). It is
a statement of fact about ordering, and ordering is exactly what a small model
gets subtly wrong while sounding certain.

### [7] Verifier — `app/pipeline/verify.py`

Nothing the model says reaches the user unchecked:

| Check | Consequence |
|---|---|
| every cited ID resolves to real evidence | invented citations stripped, confidence capped at 0.7 |
| the chosen cause is backed by signals that fired | falls back to the engine's top candidate |
| the cause does not start after the first symptom | confidence capped at 0.35 |
| model choice vs rule ranking | disagreement reported, both shown |
| top two candidates too close | confidence capped at 0.6 |
| evidence sources incomplete | confidence capped at 0.65 |
| no baseline window available | confidence capped at 0.55 |

If the model fails entirely, the deterministic ranking carries the answer and
`analyst` is set to `"deterministic"` — a degraded run is never indistinguishable
from a working one.

### [8] Store — `app/store/investigations.py`

Every run is persisted with its plan, windows, signals, candidates, verified
answer and per-stage timings. This is the audit trail, and it is what makes the
evaluation harness meaningful.

---

## The context window

`app/llm/ollama.py` sets `num_ctx` explicitly and **raises** if Ollama reports
having evaluated a prompt at the context limit.

Ollama defaults `num_ctx` to 2048 and silently truncates longer prompts, keeping
only the tail. A pipeline that sends 13,000 tokens of evidence and receives an
answer generated from the last 2,000 produces confident analysis that has seen
almost none of its input — and nothing in the response indicates it. Treating
that as a hard error rather than a warning is the single most important line in
the LLM layer.

Prompt size is bounded by construction through the `max_prompt_*` budgets rather
than by truncating afterwards.

---

## Evaluation

`python -m eval.run_eval` injects each of the ten incident scenarios, waits for
it to develop, runs a real investigation, and scores:

- **signal recall** — did the engine detect the right signals?
- **cause accuracy** — did the final answer name the right root cause?
- **service accuracy** — did it name the right *component*, not just the right
  shape of failure?

Signal recall is the leading indicator. If the engine never detected the OOM
kill, no amount of prompt work will make the answer mention it. Only once recall
is high does cause accuracy say anything about the model.

Ground truth lives in the incident controller next to the injector, so the two
cannot drift apart.
