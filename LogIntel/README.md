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
├── testbed/         Vagrant VM: k3s, fluent-bit, prometheus, a 4-service demo system
├── agent/           The analysis agent (FastAPI + pipeline), runs in WSL
├── ui/              React UI: flow-map investigations + an incident control panel
├── metrics-mirror/  One-way Prometheus -> OpenSearch mirror, for Dashboards only
├── scripts/         Windows network setup, WSL bootstrap, Dashboards setup
└── docs/            Architecture, schema contracts, and the usage guide
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

### 2. Start OpenSearch in WSL

OpenSearch must bind `0.0.0.0` (not just loopback) and be running before the VM
boots, or Fluent Bit will spend its first minutes retrying — harmless, but noisy.

### 3. Bootstrap the agent (WSL) — **before** starting the testbed

```bash
cd /mnt/d/Projects/AI-Log-Management-System/LogIntel/scripts
./bootstrap-agent.sh
```

Creates `agent/venv`, installs dependencies, writes `agent/.env` with the
endpoints detected for *this* machine, and applies the OpenSearch index
templates.

The ordering matters. If the testbed writes the first log document before those
templates exist, OpenSearch maps `system.id` dynamically as a text field, every
term filter in the pipeline then matches nothing, and the result looks exactly
like a healthy system with no logs. (`/api/health` detects this and tells you how
to fix it, but it is easier to avoid.)

### 4. Bring up the testbed (Windows PowerShell)

```powershell
cd D:\Projects\AI-Log-Management-System\LogIntel\testbed
vagrant up
```

First boot installs k3s and deploys everything; expect 5–10 minutes. It prints a
status summary at the end, and re-prints it on every subsequent `vagrant up`.

### 5. Start the agent and check the wiring

```bash
cd /mnt/d/Projects/AI-Log-Management-System/LogIntel/agent
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

```bash
curl -s localhost:8000/api/health | python3 -m json.tool
```

Every dependency reports `ok` / `degraded` / `unreachable` individually, so a
half-connected setup is obvious immediately rather than three steps later.

### 6. OpenSearch Dashboards (logs + events + a metrics mirror)

```bash
python3 scripts/setup-dashboards.py
```

Creates index patterns and a starter dashboard at
`http://localhost:5601/app/dashboards`. To have real metrics in it too (the
agent itself always reads Prometheus directly, never this):

```bash
cd metrics-mirror && docker build -t logintel-metrics-mirror .
docker run -d --name logintel-metrics-mirror --restart unless-stopped \
  --network opensearch_default \
  -e OPENSEARCH_URL=http://opensearch:9200 -e PROMETHEUS_URL=http://172.23.80.1:30090 \
  logintel-metrics-mirror
```

### 7. The React UI

```bash
cd ui && npm install && npm run dev -- --host 0.0.0.0
```

Open **http://localhost:5173**. A flow-map view of every investigation stage,
plus an **Incidents** tab that starts and stops the same scenarios the eval
harness uses, against the real cluster. See
**[docs/usage-guide.md](docs/usage-guide.md)** for how to read it.

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
