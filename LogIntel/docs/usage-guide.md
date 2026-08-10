# Using LogIntel

This is the walkthrough. [architecture.md](architecture.md) explains *why* the
pipeline is built this way; this document is about actually running it.

---

## The 30-second mental model

You have three independent systems, and LogIntel sits between them:

```
Testbed VM (k3s)          →  produces logs, events, metrics
OpenSearch + Dashboards   →  stores and lets you browse logs/events (+ a metrics mirror)
LogIntel agent + React UI →  investigates: turns a question into a verified root cause
```

The **agent** is the only thing that talks to all three. The **React UI** talks
only to the agent. OpenSearch Dashboards is a separate window you open when you
want to look at raw data yourself, outside of an investigation.

---

## One-time setup

Do this once, in order — the order matters, see the note in step 2.

1. **Windows, elevated PowerShell:**
   ```powershell
   cd D:\Projects\AI-Log-Management-System\LogIntel\scripts
   .\setup-windows-network.ps1
   ```
   Wires the testbed VM to reach OpenSearch running in WSL.

2. **WSL, before the testbed VM ever boots:**
   ```bash
   cd /mnt/d/Projects/AI-Log-Management-System/LogIntel/scripts
   ./bootstrap-agent.sh
   ```
   Creates the agent's virtualenv and applies OpenSearch's index templates.
   If the testbed writes its first document before these templates exist,
   `system.id` gets mapped as free-text instead of an exact-match field, and
   every query the agent runs afterward silently matches nothing — indistinguishable
   from "healthy system, no incidents." Doing this first avoids that entirely.

3. **Windows PowerShell:**
   ```powershell
   cd D:\Projects\AI-Log-Management-System\LogIntel\testbed
   vagrant up
   ```
   Installs k3s and deploys the demo system (`checkout-api → payment-api →
   payment-db`, plus a load generator and an incident injector). Takes 5–10
   minutes the first time.

4. **WSL — set up OpenSearch Dashboards:**
   ```bash
   cd /mnt/d/Projects/AI-Log-Management-System/LogIntel
   python3 scripts/setup-dashboards.py
   ```
   Creates index patterns and a starter dashboard for logs, events, and the
   metrics mirror (step 5). Safe to re-run any time.

5. **WSL — start the metrics mirror** (only needed once; it keeps running):
   ```bash
   cd metrics-mirror
   docker build -t logintel-metrics-mirror .
   docker run -d --name logintel-metrics-mirror --restart unless-stopped \
     --network opensearch_default \
     -e OPENSEARCH_URL=http://opensearch:9200 \
     -e PROMETHEUS_URL=http://172.23.80.1:30090 \
     logintel-metrics-mirror
   ```
   This is **not** part of the investigation pipeline — the agent reads
   Prometheus directly, always. This container exists solely so metrics show up
   in OpenSearch Dashboards next to logs and events, for you to browse. If you'd
   rather skip it, use Prometheus's own graph UI at `http://172.23.80.1:30090`
   instead — nothing about investigations changes either way.

6. **WSL — install the UI:**
   ```bash
   cd /mnt/d/Projects/AI-Log-Management-System/LogIntel/ui
   npm install
   ```

---

## Every time you sit down to use it

Three things need to be running. Each is a separate terminal.

```bash
# Terminal 1 — the agent
cd /mnt/d/Projects/AI-Log-Management-System/LogIntel/agent
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

```bash
# Terminal 2 — the UI
cd /mnt/d/Projects/AI-Log-Management-System/LogIntel/ui
npm run dev -- --host 0.0.0.0
```

```powershell
# The testbed VM — only if it isn't already running
cd D:\Projects\AI-Log-Management-System\LogIntel\testbed
vagrant up
```

Open **http://localhost:5173** (WSL2 forwards this to Windows automatically —
no extra networking needed). You should immediately see a row of colored dots
under the LogIntel title: OpenSearch, Prometheus, Ollama, Incidents VM,
Registry. Green means reachable. That strip is always visible for a reason —
if something's wrong, you'll see exactly what before you waste time on a
confusing investigation result.

---

## Using the Investigate tab

1. Pick a **system** — the dropdown only shows systems that have actually
   shipped logs (queried from OpenSearch live). `shopdemo` is the demo system.
2. **Environment** and **service** are also pulled from real data. Leaving
   service on "let the orchestrator decide" is the normal choice — it exists
   so you *can* force it when you already know, but the model resolving it
   itself from your question is what's actually being tested.
3. Type your question and hit **Analyze**.

What happens next is the **flow map** — eight boxes across the top, one per
pipeline stage, in the order they actually run. A box is grey while pending,
pulses blue while the agent is working on it, and turns green the moment its
data lands. Click any green box to see that stage's data below. You can click
around while the run is still in progress — nothing is hidden until the end.

| Stage | What you're looking at |
|---|---|
| **Plan** | What the model understood + any correction notes (e.g. it guessed a service that doesn't exist, so the system fell back) |
| **Time Window** | Where the "incident" actually is, found by scanning for a real departure from baseline — not just "the last 30 minutes" |
| **Evidence** | How much was collected, and which sources (if any) came back incomplete |
| **Signals** | Every measurement that crossed its threshold, each with its own onset time and magnitude vs. baseline |
| **Candidates** | Ranked explanations, generated by rules — not by the model |
| **LLM Analysis** | The model's pick and its stated reasoning |
| **Verify** | Whether that pick survived cross-checking |
| **Report** | Everything, assembled: narrative, timeline, next steps, and the full verification log |

### Reading the Report tab — the part that matters most

The **Report** node is where the answer lives, and it's built to show its work.
Two fields tell you how much to trust it:

- **`analyst: model (verified)` vs `deterministic fallback`** — if Ollama
  didn't answer usably, the rule-based ranking becomes the answer instead of
  failing silently. This tells you which happened.
- **Confidence** — this is not the model's stated confidence. The verifier
  recomputes it, and *lowers* it whenever something looks off: citations that
  don't resolve to real evidence, a chosen cause with no supporting signal, a
  missing baseline, an ambiguous top two candidates — or the one that matters
  most:

**"disagreed with the rule engine's top pick" / `effect_precedes_cause`.**
In one real run against this testbed, the model picked "payment-api became
unavailable" — but the verifier caught that this candidate's onset was 339
seconds *after* the first symptom it was supposed to explain, and capped
confidence at 35% with an explicit note. An effect cannot precede its cause.
That's not a bug output — that's the safety net working exactly as intended.
When you see a low-confidence report with verification notes attached, read
the notes; they tell you specifically what didn't add up, and the **Candidates**
tab will show you what the rules ranked instead.

Every panel has a **"View raw JSON"** link at the bottom. That is the actual
payload the backend sent — nothing in the readable view is summarized in a way
that hides information, it's just formatted.

---

## Using the Incidents tab

This is a control panel for the same injector the automated eval harness uses —
starting a scenario here really scales down a real pod, or really patches a
real deployment's resource limits. Nothing is simulated.

1. Pick a scenario card. Each one states what it does and what the *correct*
   answer is supposed to be (`expects: dependency failure`, etc.) — that's the
   ground truth, so you can judge the investigation yourself once it finishes.
2. Click **Start incident**. The card shows a live "developing… Ns / ~Ns"
   countdown — this is the time the fault needs to actually manifest (a
   CrashLoopBackOff needs a few restart cycles; an error-rate spike is visible
   almost immediately).
3. Once it says **"active — ready to investigate"**, click **Investigate now**.
   This switches to the Investigate tab with the system, service and question
   pre-filled, ready to submit.
4. When you're done, **Stop** the individual scenario, or **Reset all** to
   clear everything and return the system to its baseline state. Do this
   before starting a different scenario — running two at once makes the
   evidence genuinely ambiguous, and the pipeline will tell you so (correctly).

---

## OpenSearch Dashboards

**http://localhost:5601** → *Dashboards → LogIntel Overview*.

Three index patterns exist: `logintel-logs-*`, `logintel-events-*`,
`logintel-metrics-mirror-*`. The starter dashboard has five panels — log volume
by level, Kubernetes event reasons, top error messages, CPU usage, and HTTP
error rate — but this is a starting point, not the whole story. Use
**Discover** (left menu) to browse raw documents against any of the three
patterns; it's the fastest way to see a field you're curious about.

One thing worth remembering: the metrics you see here are a **mirror**, polled
every 30 seconds purely for browsing. The agent's actual investigations never
read this index — they query Prometheus directly, because `rate()` and
percentile calculations need a real time-series database. If a number looks
slightly stale by a few seconds compared to what an investigation reports,
that's why, and it's expected.

---

## Diagnosing the pipeline itself

When a result surprises you, the question is always *which stage* went wrong —
retrieval, feature extraction, or the model. Three tools answer that.

```bash
cd agent && source venv/bin/activate

python -m eval.probe state 30    # what is actually in the indices right now
python -m eval.probe tools 30    # run every tool for a 30m window and print what it returns
python -m eval.probe prom        # check each PromQL template returns series
```

`probe tools` is the most useful of the three: it prints the chosen windows, the
log patterns, the discovered dependency graph, every metric series, and every
signal that fired — the complete input to the reasoning stages. If the signal you
expected isn't in that list, the problem is upstream of the model and no prompt
change will fix it.

```bash
python -m eval.explain                  # full trace of the most recent investigation
python -m eval.explain inv-073480decb58 # a specific one
```

Shows the windows, all signals with their magnitudes, how the rules ranked every
candidate, which one the model picked, and every verification check. This is how
you tell "the engine never detected it" from "the engine detected it and the
model chose badly" — two problems with completely different fixes.

```bash
python -m eval.audit                    # look across all stored runs
```

Aggregates every persisted investigation and reports which verification codes
keep recurring. A code appearing on most runs is a pipeline bug, not a run of bad
luck — when this was first run it showed `incomplete_evidence` firing on 100% of
runs and `effect_precedes_cause` on 82%, both of which turned out to be defects
rather than genuine findings.

## Measuring whether a change actually helped

Every investigation is persisted to OpenSearch (`logintel-investigations`), and
the ten incident scenarios double as a labeled test set:

```bash
cd agent && source venv/bin/activate
python -m eval.run_eval                       # all ten scenarios
python -m eval.run_eval --scenario crashloop   # just one
```

This injects each scenario for real, waits for it to develop, runs a real
investigation, and scores two things **separately**:

- **signal recall** — did the deterministic engine detect the right signals at all?
- **cause accuracy** — did the final, verified answer name the right root cause?

Check recall first. If the engine never detected the OOM kill, no amount of
prompt tuning will make the final answer mention it — the fix is in
`app/pipeline/signals.py`, not in a prompt. Only once recall is consistently
high does cause accuracy start to say something meaningful about the model.

### Why an evaluation run is slow, and why that matters

Most of the wall-clock time is the harness waiting between scenarios, and it is
not padding. Two things have to be true before the next incident is injected:

- **The error rate is back to baseline** — but note that a healthy `shopdemo`
  still sits at roughly 4 errors/min, because each service has a 0.5% base error
  rate and it compounds across the three tiers.
- **Every pod is Ready.** A low error rate alone is not evidence of health: a
  crashlooping service serves almost no traffic and therefore produces almost no
  errors, so the rate check passes while the system is still broken.

Both must then hold *continuously* for `--quiet-hold` seconds (default 180).
This is the part that cannot be shortened without invalidating the results. The
pipeline places its baseline window immediately *before* the incident — which is
to say, before the moment the system went quiet — so injecting one minute after a
reset puts the previous scenario inside this scenario's baseline. Every
comparison the signal engine makes is against that baseline.

The failure mode is not subtle once you know to look for it, but it is invisible
in the score: the run reports a confident wrong answer, and the answer is a
perfectly reasonable reading of contaminated evidence. If a scenario fails,
check its windows in `eval.explain` before assuming the agent is at fault.

---

## When something looks wrong

Start with the health strip at the top of the UI — it names the broken
component and, on hover, usually the fix. A few specific ones:

- **OpenSearch shows "degraded" with a mapping-conflict message** — an index
  got created before the templates were applied (see setup step 2). Delete the
  named index; it will recreate correctly on the next document.
- **Ollama shows "degraded"** — either the model isn't pulled
  (`ollama pull qwen2.5-coder` on the Windows host), or Ollama isn't listening
  on `0.0.0.0` so WSL can't reach it (`OLLAMA_HOST=0.0.0.0` on Windows, then
  restart the Ollama app).
- **A report's `analyst` says `deterministic fallback`** — the model call
  either failed or timed out; check the agent's terminal log for the exact
  reason, and see `agent/.env`'s `OLLAMA_TIMEOUT` / `OLLAMA_NUM_CTX`.
- **Incidents VM shows unreachable** — the testbed VM is down or its NAT
  forward isn't up: `cd testbed && vagrant status`, and `vagrant up` if needed.
