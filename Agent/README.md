# AI Log Analysis System - Phase 1: Orchestrator Agent

This project implements the foundational layer of the AI Log Analysis System, containing a minimal React frontend and a Python FastAPI backend powered by an Orchestrator Agent.

## Architecture

```text
React UI
    ↓
Python FastAPI Backend
    ↓
Orchestrator Agent
    ↓
LLM Interface
    ↓
Ollama
    ↓
Qwen 2.5 Coder
```

## Prerequisites
- Node.js & npm (for the frontend)
- Python 3.11+ (for the backend)
- Ollama running locally (on Windows or the host machine) with the `qwen2.5-coder` model pulled.

## Ollama & Qwen Model Setup
Ensure Ollama is running on your host machine.
Pull the required model:
```bash
ollama run qwen2.5-coder
```

## Backend Installation
1. Navigate to the backend directory:
   ```bash
   cd Agent/backend
   ```
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3. (Optional) Configure environment variables. By default, it expects Ollama at `http://localhost:11434`.

## Frontend Installation
1. Navigate to the frontend directory:
   ```bash
   cd Agent/frontend
   ```
2. Install dependencies:
   ```bash
   npm install
   ```

## Running the Application
**Backend:**
```bash
cd Agent/backend
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
*Note: The backend will be accessible at http://localhost:8000*

**Frontend:**
```bash
cd Agent/frontend
npm run dev
```
*Note: The frontend will be accessible at http://localhost:5173*

## API Example
You can POST to `/api/investigations`:
```json
{
  "system_id": "ecommerce-platform",
  "system_name": "E-Commerce Platform",
  "environment": "production",
  "question": "Why is payment-api failing?"
}
```

## Testing
Run the backend unit tests (which use a mocked FakeLLM to test the Orchestrator safely):
```bash
cd Agent/backend
pytest
```

## Current Limitations
- This is Phase 1. The agent only translates natural language questions into an `InvestigationPlan`.
- The agent does not execute the plan, query OpenSearch, check Kubernetes, or fetch logs.
- The UI system list is currently mocked.

## Planned Future Components
- Dispatcher
- Application Log Tools, Kubernetes Event Tools, Metrics Tools
- Correlation Engine
- Analysis Agent
- RCA Agent
