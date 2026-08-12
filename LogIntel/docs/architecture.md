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
   ├─[6] Reasoning loop ─────────── LLM (ReAct)     think → call a tool → observe → repeat
   │
   ├─[7] Verifier ───────────────── deterministic   citations resolved, confidence rebuilt from caps
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

Several details make it work, and each was added because its absence produced a
specific wrong answer against the live testbed:

- **Median and MAD, not mean and standard deviation.** Error counts are spiky; a
  single burst inflates a standard deviation enough to hide the thing you are
  looking for.
- **The baseline is estimated from the quiet 60% of the range.** When an incident
  fills half the window it drags the median up with it, and a threshold derived
  from that median sits above the very spike it should catch.
- **The spread is floored at `sqrt(median)`.** These are event counts, whose
  natural variation is roughly the square root of the mean even when nothing is
  wrong. MAD alone badly understates that: a service sitting at 3 errors/min has
  a MAD near 1, putting the threshold at 7 — which routine Poisson noise crosses
  for four minutes at a stretch. Without this floor the pipeline declared a
  96-minute incident on a perfectly healthy system.
- **Onsets must be sustained**, and must **separate two regimes**: the stretch
  after a candidate onset has to be `onset_min_elevation` times busier than the
  stretch before it. A crossing that fails this is skipped and the scan
  *continues* — rejecting a noisy blip must not mean abandoning the search, or a
  real failure later in the range is never even considered.
- **When the range ends elevated, the search runs backwards** to find where the
  episode still in progress began. Scanning forward returns the oldest departure
  anywhere in range, which after an earlier incident has resolved is the wrong
  one — and it drags the window across both, so every signal ends up measured
  against a baseline containing the previous failure. The backward walk tolerates
  dips (incidents fluctuate) with a continuation bar that scales to the episode's
  own magnitude, because a fixed bar chains straight back through ordinary noise.
- **The incident window is capped at twice the requested range.** The search
  deliberately looks back several times further than asked, but the answer has to
  stay close to the question: a 15-minute question should not return a 60-minute
  window anchored on something that resolved an hour ago.

It also distinguishes outcomes that are genuinely different and must not be
conflated: an onset was found; errors were *never quiet* in the whole search
range (so the incident predates everything visible); the departure began further
back than the period asked about; or nothing departed at all.

Without a baseline, "is this abnormal?" is unanswerable — which is why the
verifier caps confidence whenever one could not be established.

### [3] Evidence Tools — `app/tools/`

Aggregation-first. Nothing bulk-fetches raw documents.

| Tool | Returns |
|---|---|
| `LogTool.histogram` | per-minute counts by level |
| `LogTool.collect` | message **patterns** with counts, onsets and baseline counts, plus the **call graph** |
| `LogTool.samples` | a few raw error lines from the *start* of the incident |
| `EventTool` | events preserving `reason`, `type`, `count`, `first_timestamp` |
| `MetricTool` | 16 PromQL templates, summarised for both windows |

**The call graph is discovered, not configured.** Services log the dependency
they called (`dependency.name`), so one aggregation yields who calls whom —
`checkout-api → payment-api → payment-db`. That is what lets root-cause
attribution follow the direction of the arrows instead of guessing from onset
times, and onset times genuinely are a bad guide here: when a dependency slows
down, a *caller* frequently trips its latency threshold first.

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
- **Pre-existing conditions are marked as such.** A condition already running
  when the window opened has its onset clamped to the window start, and carries
  `pre_existing=True`. Both halves matter: without the clamp a probe failure from
  three hours earlier outranks everything by being oldest, and without the flag
  it outranks everything by then sitting exactly at the front of the range. It
  cannot have triggered the incident either way, so the hypothesis engine
  excludes it from causal reasoning entirely.

### [5] Hypothesis Engine — `app/pipeline/hypotheses.py`

Ten rules over the signal set, each producing a candidate with supporting *and*
contradicting signals. Ranking is causal:

- a candidate whose onset precedes the first symptom gains score;
- one that starts after the symptoms loses it, because an explanation that
  begins after the thing it explains is not an explanation;
- the earliest-onset candidate gains a further bonus;
- candidates that say the same thing about the same component are merged, so two
  signals pointing at one failure do not look like two competing explanations.

**Attribution follows the call graph, not onset order.** Errors and latency both
propagate *upward*: when a component fails, everything that calls it fails too,
and the entry point is usually the loudest and often the first to cross a
threshold. Rules that would otherwise pick the first or largest signal instead
pick the **deepest** service in the discovered call graph. Two live failures made
the case for this: a `db-latency` injection where `checkout-api` tripped its
latency threshold before the database causing the delay, and a `payment-5xx`
injection where the engine confidently blamed `checkout-api` for 500s that
originated one tier below it.

This is where "the database went down, and the two services above it reporting
errors are symptoms" gets decided — in Python, reproducibly.

### [6] Reasoning loop — `app/agents/react.py`, `app/agents/tool_bindings.py`

A ReAct loop: think, call a tool, read the observation, repeat. It decides what
to look at and how to explain it. It does **not** measure anything — the tools
hand it figures already compared against a baseline and against resource limits,
and any number it produces on its own is treated as unsupported.

Two properties make the loop's output checkable rather than merely plausible:

**Every tool returns evidence IDs, and shows them.** A tool that returns prose
the model can only paraphrase produces an answer nobody can verify. The
observation text leads each row with the ID the model is expected to cite. The
"and shows them" half is not obvious: `search_logs` originally tracked pattern
IDs internally without printing them, and the model — with nothing to copy —
fabricated `met:ERROR payment-api`. An ID the loop must cite but is never shown
guarantees an unverifiable answer.

**Empty results explain themselves.** `Nothing matched` on its own makes the loop
guess, and it guesses badly: one live run spent five steps re-searching the same
term at every log level in turn. The tools now say what *is* present, and name
the specific mistake when a level name is passed as a text query. That turned a
five-step dead end into a one-step correction.

The loop also detects two kinds of thrashing — an identical repeated call, and a
tool that keeps returning nothing under varied arguments — and tells the model to
stop rather than silently burning its step budget.

The answer mode is chosen from the plan's intent, not by the model. "List the 5xx
from payment-api" is a retrieval request; answering it with a root-cause report
is a wrong answer however well argued. Extraction and aggregation answers carry a
`table` of the actual records.

### [7] Verifier — `app/pipeline/answer_check.py`

Nothing the model says reaches the user unchecked. Confidence is **rebuilt**, not
accepted:

| Check | Consequence |
|---|---|
| a cited ID resolves to real evidence | unresolved citations kept but marked; capped at 0.6 |
| a citation is a copied schema placeholder | distinguished from fabrication; capped at 0.75 |
| a reasoning step cites nothing | capped at 0.65 — unless there was nothing to cite |
| the answer names a component the rules also ranked first | +0.1, the only thing that raises confidence |
| the answer names a different component | disagreement recorded; capped at 0.6 |
| no baseline window available | capped at 0.55 |
| evidence sources incomplete | capped at 0.7 |
| the loop failed or hit its step limit | capped at 0.35 |
| any answer at all | capped at 0.9 — nothing here is certain |

Caps and bonuses are accumulated separately and combined at the end. Applying
them in call order was a real bug: a truncated prompt earned its 0.35 cap and
then agreement with the rule engine added 0.1 back, shipping a broken run at 0.5.
A cap is a statement about what the run can support, and nothing may lift the
answer past it.

Every adjustment is returned as a `ConfidenceFactor` the reader can see and
argue with — a bare number is not accountable.

If the loop fails entirely, the deterministic ranking carries the answer and
`analyst` becomes `"react (degraded)"` — a degraded run is never indistinguishable
from a working one.

The timeline is **not** generated by the model (`app/pipeline/timeline.py`). It is
a statement of fact about ordering, and ordering is exactly what a small model
gets subtly wrong while sounding certain.

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
