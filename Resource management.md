# Resource Allocation Plan for LogIntel Central Cluster

This report outlines the proposed CPU and Memory allocations for your entire Rancher-deployed central cluster. Setting proper requests and limits prevents individual services (like Ollama or OpenSearch) from starving others and crashing your cluster.

## Proposed Allocations

The following limits will be applied to your `stateless.yaml` and `stateful.yaml` files, as well as your Ollama deployment.

### 1. Stateful Services (`stateful.yaml`)
These are your heavy database and metrics services.

| Service | Requests (Guaranteed) | Limits (Maximum) | Justification |
| :--- | :--- | :--- | :--- |
| **OpenSearch** | CPU: 1<br>RAM: 2Gi | CPU: 2<br>RAM: 4Gi | *(Already configured in your file)* Heavy Java database. Needs minimum 2GB to function properly. |
| **Postgres** | CPU: 100m<br>RAM: 128Mi | CPU: 500m<br>RAM: 512Mi | Relational database for your gateway. |
| **Prometheus** | CPU: 100m<br>RAM: 256Mi | CPU: 500m<br>RAM: 1Gi | Metric timeseries database. Can spike during high log volume. |

---

### 2. Stateless Services (`stateless.yaml`)
These are your frontend, APIs, and AI integrations.

| Service | Requests (Guaranteed) | Limits (Maximum) | Justification |
| :--- | :--- | :--- | :--- |
| **Gateway** (Node.js) | CPU: 100m<br>RAM: 128Mi | CPU: 500m<br>RAM: 256Mi | Handles log ingestion. CPU bound, low memory footprint. |
| **Dashboards** (Node.js) | CPU: 100m<br>RAM: 512Mi | CPU: 500m<br>RAM: 1Gi | The OpenSearch UI. Needs a bit more RAM to render large datasets. |
| **Agent (AI API)** (Python) | CPU: 100m<br>RAM: 128Mi | CPU: 500m<br>RAM: 512Mi | Python API that talks to Ollama. |
| **UI** (Nginx) | CPU: 10m<br>RAM: 32Mi | CPU: 100m<br>RAM: 64Mi | Extremely lightweight static file server. |
| **Metrics-Mirror** | CPU: 50m<br>RAM: 64Mi | CPU: 200m<br>RAM: 128Mi | Lightweight background sync process. |

---

### 3. AI Workloads (Ollama)
Your heavy-duty LLM model runner. These numbers are tuned for an 8B model (like Qwen2.5 or Llama 3) running on CPU.

| Service | Requests (Guaranteed) | Limits (Maximum) | Justification |
| :--- | :--- | :--- | :--- |
| **Ollama** | CPU: 2000m<br>RAM: 6Gi | CPU: 4000m<br>RAM: 10Gi | Massive memory needed to load weights into RAM. Generous CPU required for acceptable token generation speed. |

> [!NOTE]
> **Total Maximum Capacity (Limits):** If everything maxes out simultaneously including Ollama, this setup will consume roughly **18.5 GB of RAM** and **9 CPU cores**. Make sure your Rancher worker nodes have at least 24GB of total system RAM to comfortably host this entire stack!

## User Review Required

Please review the proposed limits above. If you approve, click **Proceed**, and I will automatically inject these `resources:` blocks into your `stateful.yaml` and `stateless.yaml` files exactly where they belong!
