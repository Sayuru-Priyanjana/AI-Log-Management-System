# LogIntel

A ground-up rebuild of the AI log analysis system, designed around one principle:

> With a small local model, accuracy comes from **retrieval and deterministic feature
> extraction**, not from prompting.

Everything measurable is measured in Python. The LLM has exactly two narrow jobs:
turn a question into a constrained plan, and **choose among pre-computed candidate
causes** and explain the choice. It can never assert a cause that no deterministic
signal supports.

See [docs/architecture.md](docs/architecture.md) for the full pipeline,
[docs/data-schema.md](docs/data-schema.md) for the document contracts, and
**[docs/usage-guide.md](docs/usage-guide.md) for the full walkthrough** —
start there if you just want to run the thing.

---

## Layout

```
LogIntel/
├── docker-compose.yml  The stack: OpenSearch, Dashboards, agent, UI
├── testbed/            Vagrant VM: k3s, fluent-bit, prometheus, a 4-service demo system
├── agent/              The analysis agent (FastAPI + pipeline) + its Dockerfile
├── ui/                 React UI + its Dockerfile and nginx front door
├── metrics-mirror/     One-way Prometheus -> OpenSearch mirror, for Dashboards only
├── scripts/            Windows network setup, WSL bootstrap, Dashboards setup
└── docs/               Architecture, schema contracts, and the usage guide
```

## Topology

```
                      Windows host
   ┌────────────────────────────────────────────────────┐
   │  portproxy   192.168.56.1:9200 → 127.0.0.1:9200    │
   │  VBox NAT    0.0.0.0:30090/30099 → VM              │
   └────────────────────────────────────────────────────┘
       ▲ 192.168.56.1                    ▲ 172.23.80.1
       │ host-only                       │ WSL gateway
  ┌────┴─────────────────┐        ┌──────┴──────────────────────────┐
  │ VM  192.168.56.20    │        │ WSL Ubuntu                      │
  │ 3 GB RAM / 2 vCPU    │        │ OpenSearch :9200  Dashboards :5601
  │                      │─logs──▶│ LogIntel agent :8000            │
  │ k3s + fluent-bit     │        │ React UI    :5173                │
  │ prometheus + KSM     │◀─┐     │ metrics-mirror (docker, no port) │
  │ event-collector      │  │     └──────────────────────────────────┘
  │ checkout/payment/db  │  │PromQL      ▲          ▲
  │ incident-controller  │◀─┼────────────┘          │ Ollama :11434 (Windows)
  └──────────────────────┘  └────────────────────────
```

The React UI talks only to the agent (`:8000`); the agent is the only thing
that talks to OpenSearch, Prometheus, Ollama and the VM's incident controller.
`metrics-mirror` is a one-way, read-only copy of a few Prometheus series into
OpenSearch purely so Dashboards can show them next to logs and events — the
agent's actual investigations always query Prometheus directly.

Why the network is wired this way: the WSL2 IP changes on every reboot, so
nothing is allowed to depend on it. The VM reaches OpenSearch through the
**fixed** VirtualBox host-only gateway (`192.168.56.1`), and the agent reaches
the VM through VirtualBox NAT forwards bound to `0.0.0.0`, which WSL sees at
its default gateway.

---

## Quick start

### 1. Windows (PowerShell **as Administrator**, once)

```powershell
cd D:\Projects\AI-Log-Management-System\LogIntel\scripts
.\setup-windows-network.ps1
```

Adds the portproxy and firewall rules so the VM can reach OpenSearch in WSL.
Re-run it after a Windows reboot if OpenSearch moves; `-Remove` undoes it.

### 2. Bring up the stack

```bash
cd /mnt/d/Projects/AI-Log-Management-System/LogIntel
docker compose up -d --build
```

Four services and two helpers, on one network:

| | | |
| --- | --- | --- |
| UI | http://localhost:5173 | nginx serving the bundle and proxying `/api` to the agent |
| Configuration | http://localhost:5173/configuration | connection status and settings for every dependency |
| Agent API | http://localhost:8000/docs | the pipeline |
| Dashboards | http://localhost:5601 | Discover and an overview dashboard, set up automatically |
| OpenSearch | http://localhost:9200 | logs, events, and the stored investigations |

`dashboards-setup` creates the index patterns and dashboard, then exits;
`metrics-mirror` copies a fixed set of Prometheus series into OpenSearch so
Dashboards can chart metrics beside the logs. The agent never reads that mirror
— it queries Prometheus directly, and a mirror it depended on would be a second
source of truth to keep honest.

The UI talks to its own origin, so there is no agent URL in the bundle and no
CORS to configure. The published agent port exists for `curl` and the eval
harness.

**Two things stay outside**, because they cannot sensibly live in a compose file
that gets torn down: **Ollama**, which wants a GPU and a multi-gigabyte model
cache, and **Prometheus**, which runs inside the testbed VM scraping pods this
network cannot see. Both are reached at `host.docker.internal`, which is the
Windows host. Override when they live elsewhere:

```bash
PROMETHEUS_URL=http://192.168.56.20:30090 docker compose up -d
```

> If you have been running the standalone stack in `~/opensearch`, bring it down
> first — `docker compose -f ~/opensearch/docker-compose.yml down`. This file
> adopts the same data volume by default, so the existing indices carry over
> rather than being duplicated into a second cluster. `OPENSEARCH_VOLUME=fresh`
> starts empty instead.

### 3. Check the wiring

```bash
curl -s localhost:8000/api/health | python3 -m json.tool
```

Every dependency reports `ok` / `degraded` / `unreachable` individually, so a
half-connected setup is obvious immediately rather than three steps later.

The ordering matters: the agent applies the OpenSearch index templates at
startup, and it must do so **before** the testbed writes its first document. If
a log arrives first, OpenSearch maps `system.id` dynamically as a text field,
every term filter in the pipeline then matches nothing, and the result looks
exactly like a healthy system with no logs. (`/api/health` detects this and says
how to fix it, but it is easier to avoid.)

### 3a. The configuration page

**http://localhost:5173/configuration** is the connection surface: the live
status of every dependency, a *Test* button per connection, and editable
settings for all of them — which OpenSearch, which model, which time zone. A
change takes effect immediately (the clients are rebuilt in place) and is
persisted to OpenSearch, so it survives a restart.

Three things are deliberately true of it:

- **Every field says where its value came from** — `default`, `environment`, or
  `saved`. An override written months ago silently shadowing an environment
  variable someone is staring at is how an afternoon disappears.
- **Thresholds are not editable.** Signal multipliers and window arithmetic stay
  in code and tests. A text box that quietly changed what counts as an incident
  would make every stored investigation incomparable with the next.
- **API keys are write-only.** They can be set or replaced, never read back; the
  API reports whether one is present, not what it is.

The page also lists the clusters shipping data in, and the field contract a new
one has to satisfy. There is no registration step — a cluster appears once its
logs arrive — but a cluster that ships without `system.id`, `service.name` and
`level` is visible in Discover and invisible to the agent, which looks exactly
like a healthy system with no logs.

### 3b. Theme and time zone

The header carries a light/dark toggle; with no stored choice it follows the
operating system.

Times are shown in **+05:30** by default, changeable on the configuration page
to any offset or IANA name. Everything is *stored and compared* in UTC — windows
and baselines are arithmetic, and an offset that shifts twice a year would
corrupt it — but nothing is *displayed* in UTC unless you ask. That applies to
the agent's own prose too, not just the page: an answer reading "the departure
began at 05:12" beside a dashboard reading 10:42 is two facts the reader has to
reconcile by hand, every time.

### 3c. Choosing where the model runs

A **local Ollama is the default**: nothing leaves the machine, and an
investigation that makes eight tool calls costs nothing to run. To use a hosted
model instead, set three variables and restart — nothing else changes:

| `LLM_PROVIDER` | what it talks to | also set |
| --- | --- | --- |
| `ollama` *(default)* | a local Ollama | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` |
| `openai` | any OpenAI-compatible endpoint — OpenAI, Groq, OpenRouter, Together, DeepSeek, or a local vLLM / LM Studio | `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL` |
| `anthropic` | the Anthropic Messages API | `LLM_MODEL`, `LLM_API_KEY` |

```bash
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=sk-...
# only when the endpoint is not OpenAI itself
LLM_BASE_URL=https://api.groq.com/openai/v1
```

A larger hosted model follows the ReAct protocol more reliably and writes better
prose. It does not make the answers more trustworthy on its own: signals,
windows and the verifier are the same code, and every claim is checked against
the same evidence whichever model produced it. `GET /api/health` reports which
backend is in force under `components.model`.

### 3d. Working on the code

The compose stack is the deployment. For development, run either half from
source against the same containers:

```bash
cd agent && source venv/bin/activate     # created by scripts/bootstrap-agent.sh
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

cd ui && npm install && npm run dev      # talks to the agent on :8000
```

Both dev servers want ports the stack already publishes, so stop the container
you are replacing first: `docker compose stop ui` (or `agent`). The dev UI has
no nginx in front of it, so it calls `http://localhost:8000` directly — which is
why the agent's port is published at all.

### 4. Bring up the testbed (Windows PowerShell)

```powershell
cd D:\Projects\AI-Log-Management-System\LogIntel\testbed
vagrant up
```

First boot installs k3s and deploys everything; expect 5–10 minutes. It prints a
status summary at the end, and re-prints it on every subsequent `vagrant up`.

Once logs start arriving, open **http://localhost:5173** and ask a question.
**http://localhost:5173/incidents** starts and stops the same scenarios the eval
harness uses, against the real cluster — deliberately not linked from anywhere,
because it breaks a running system on purpose. See
**[docs/usage-guide.md](docs/usage-guide.md)** for how to read the results.

---

## Running an investigation from the command line

The UI is the recommended path, but every API call it makes is one curl away:

```bash
curl -sN -X POST localhost:8000/api/investigations \
  -H 'Content-Type: application/json' \
  -d '{"system_id":"shopdemo","environment":"staging",
       "question":"why is checkout failing in the last 30 minutes?"}'
```

The response is an NDJSON stream, one line per pipeline stage
(`plan`, `windows`, `evidence`, `signals`, `candidates`, `analysis`, `verified`, `result`),
so you can watch each stage land instead of waiting for the whole run.

## Injecting an incident

Same thing — the UI's Incidents tab is a thin layer over the agent's own proxy
routes, which forward to the VM:

```bash
curl -X POST localhost:8000/api/incidents/dependency-outage/start
curl -X POST localhost:8000/api/incidents/dependency-outage/stop
curl -s     localhost:8000/api/incidents          # catalogue + active state
```

## Measuring accuracy

```bash
cd agent && source venv/bin/activate
python -m eval.run_eval                       # every scenario
python -m eval.run_eval --scenario crashloop  # just one
```

The harness injects a scenario, waits for it to develop, runs a real
investigation, and scores two things separately:

- **signal recall** — did the deterministic engine detect the right signal?
- **cause accuracy** — did the final answer name the right root cause?

Signal recall is the leading indicator. If it is low, no prompt work will help.
