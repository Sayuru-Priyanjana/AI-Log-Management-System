// Every request goes through the agent's own API — the browser never talks to
// OpenSearch, Prometheus, Ollama, or the testbed's incident controller
// directly. That keeps this file the single place network wiring lives, and
// means /api/health can report on all of them without the browser needing
// separate credentials or CORS setup for each.
// An *empty* VITE_AGENT_URL is meaningful and distinct from an unset one: it
// means "same origin", which is how the container image is built — nginx serves
// this bundle and proxies /api to the agent, so the browser never learns the
// agent's address and there is no CORS to configure. Unset means the dev server,
// where Vite is on 5173 and the agent is a separate process on 8000.
const configured = import.meta.env.VITE_AGENT_URL;
const BASE_URL = configured === undefined ? 'http://localhost:8000' : configured;

async function getJSON(path) {
  const response = await fetch(`${BASE_URL}${path}`);
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new Error(`GET ${path} -> ${response.status}: ${body.slice(0, 300)}`);
  }
  return response.json();
}

async function postJSON(path, body) {
  const response = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const text = await response.text().catch(() => '');
    let detail = text;
    try { detail = JSON.parse(text).detail || text; } catch { /* not json */ }
    throw new Error(detail || `POST ${path} -> ${response.status}`);
  }
  return response.json();
}

async function putJSON(path, body) {
  const response = await fetch(`${BASE_URL}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const text = await response.text().catch(() => '');
  if (!response.ok) {
    let detail = text;
    try { detail = JSON.parse(text).detail || text; } catch { /* not json */ }
    throw new Error(detail || `PUT ${path} -> ${response.status}`);
  }
  return text ? JSON.parse(text) : {};
}

export const getHealth = () => getJSON('/api/health');
export const getSystems = () => getJSON('/api/systems');
export const getConfig = () => getJSON('/api/config');
export const getRecentInvestigations = (limit = 20) =>
  getJSON(`/api/investigations?limit=${limit}`);

export const getIncidents = () => getJSON('/api/incidents');
export const startIncident = (id) => postJSON(`/api/incidents/${id}/start`);
export const stopIncident = (id) => postJSON(`/api/incidents/${id}/stop`);
export const resetIncidents = () => postJSON('/api/incidents/reset-all');

/**
 * Streams an investigation as NDJSON, calling onEvent for every line as it
 * arrives — this is what makes the flow map light up stage by stage instead
 * of waiting for the whole run and rendering it all at once.
 */
export async function runInvestigation(payload, { onEvent, signal }) {
  const response = await fetch(`${BASE_URL}/api/investigations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`Server responded with ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        onEvent(JSON.parse(line));
      } catch {
        console.error('Could not parse NDJSON line:', line);
      }
    }
  }
}

/**
 * Runs a single tool against a finished investigation's evidence.
 *
 * This is what a "next step" button calls. It rebuilds the evidence from the
 * plan and windows the investigation stored, so the result comes back in a
 * second or two with no model call — and against the same window the conclusion
 * was drawn from, which is the point of following up on it at all.
 */
export const runInvestigationTool = (investigationId, tool, toolInput) =>
  postJSON(`/api/investigations/${investigationId}/run-tool`, {
    tool,
    tool_input: toolInput || {},
  });

export { BASE_URL };

// -- configuration ----------------------------------------------------------
export const getSettings = () => getJSON('/api/settings');
export const getClusters = () => getJSON('/api/clusters');
export const updateSettings = (values) => putJSON('/api/settings', { values });
export const testConnection = (target) => postJSON('/api/settings/test', { target });
export const refreshSystems = () => postJSON('/api/systems/refresh');
