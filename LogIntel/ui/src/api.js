// Every request goes through the agent's own API — the browser never talks to
// OpenSearch, Prometheus, Ollama, or the testbed's incident controller
// directly. That keeps this file the single place network wiring lives.
const configured = import.meta.env.VITE_AGENT_URL;
const BASE_URL = configured === undefined ? 'http://localhost:8000' : configured;

function getHeaders(custom = {}) {
  const token = localStorage.getItem('jwt');
  const headers = { ...custom };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

function handleAuthError(response) {
  if (response.status === 401 || response.status === 403) {
    localStorage.removeItem('jwt');
    localStorage.removeItem('role');
    // If not already on login page, redirect
    if (window.location.pathname !== '/') {
      window.location.href = '/';
    }
  }
}

export async function getSystemSnapshot(systemId) {
  return getJSON(`/api/systems/${encodeURIComponent(systemId)}/snapshot`);
}

export const getSystemAlerts = (id) => getJSON(`/api/systems/${id}/alerts`);

async function getJSON(path) {
  const response = await fetch(`${BASE_URL}${path}`, { headers: getHeaders() });
  handleAuthError(response);
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new Error(`GET ${path} -> ${response.status}: ${body.slice(0, 300)}`);
  }
  return response.json();
}

async function postJSON(path, body) {
  const response = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: getHeaders(body ? { 'Content-Type': 'application/json' } : {}),
    body: body ? JSON.stringify(body) : undefined,
  });
  handleAuthError(response);
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
    headers: getHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  handleAuthError(response);
  const text = await response.text().catch(() => '');
  if (!response.ok) {
    let detail = text;
    try { detail = JSON.parse(text).detail || text; } catch { /* not json */ }
    throw new Error(detail || `PUT ${path} -> ${response.status}`);
  }
  return text ? JSON.parse(text) : {};
}

async function deleteJSON(path) {
  const response = await fetch(`${BASE_URL}${path}`, {
    method: 'DELETE',
    headers: getHeaders(),
  });
  handleAuthError(response);
  const text = await response.text().catch(() => '');
  if (!response.ok) {
    let detail = text;
    try { detail = JSON.parse(text).detail || text; } catch { /* not json */ }
    throw new Error(detail || `DELETE ${path} -> ${response.status}`);
  }
  return text ? JSON.parse(text) : {};
}

export const getHealth = () => getJSON('/api/health');
export const getSystems = () => getJSON('/api/systems');
export const getConfig = () => getJSON('/api/config');
export const getRecentInvestigations = (limit = 20, systemId) => {
  const params = new URLSearchParams({ limit: String(limit) });
  if (systemId) params.set('system_id', systemId);
  return getJSON(`/api/investigations?${params}`);
};
export const getInvestigation = (id) => getJSON(`/api/investigations/${id}`);
export const deleteInvestigation = (id) =>
  deleteJSON(`/api/investigations/${id}`);

export const getIncidents = () => getJSON('/api/incidents');
export const startIncident = (id) => postJSON(`/api/incidents/${id}/start`);
export const stopIncident = (id) => postJSON(`/api/incidents/${id}/stop`);
export const resetIncidents = () => postJSON('/api/incidents/reset-all');

export async function runInvestigation(payload, { onEvent, signal }) {
  const response = await fetch(`${BASE_URL}/api/investigations`, {
    method: 'POST',
    headers: getHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(payload),
    signal,
  });
  handleAuthError(response);
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

export const runInvestigationTool = (investigationId, tool, toolInput) =>
  postJSON(`/api/investigations/${investigationId}/run-tool`, {
    tool,
    tool_input: toolInput || {},
  });

export { BASE_URL };

export const getSettings = () => getJSON('/api/settings');
export const getClusters = () => getJSON('/api/clusters');
export const updateSettings = (values) => putJSON('/api/settings', { values });
export const testConnection = (target) => postJSON('/api/settings/test', { target });
export const refreshSystems = () => postJSON('/api/systems/refresh');
export const getSystemMetricsRequests = (id, start, end) => getJSON(`/api/systems/${id}/metrics/requests?start=${start}&end=${end}`);
export const getSystemMetricsRam = (id, start, end) => getJSON(`/api/systems/${id}/metrics/ram?start=${start}&end=${end}`);
export const getSystemMetricsLogs = (id, start, end) => getJSON(`/api/systems/${id}/metrics/logs?start=${start}&end=${end}`);
export const getTopErrors = (id, start, end) => getJSON(`/api/systems/${id}/errors/top?start=${start}&end=${end}`);
export const getLogsContext = (id, timestamp, service) => getJSON(`/api/systems/${id}/logs/context?timestamp=${timestamp}&service=${encodeURIComponent(service)}`);

export async function getSystemLogs(systemId, params = {}) {
  const queryParams = new URLSearchParams();
  if (params.query) queryParams.append('query', params.query);
  if (params.service) queryParams.append('service', params.service);
  if (params.level) queryParams.append('level', params.level);
  if (params.limit) queryParams.append('limit', params.limit);
  
  return await getJSON(`/api/systems/${systemId}/logs?${queryParams.toString()}`);
}

// Per-system integrations (Teams channel, automation) — unlike /api/settings,
// scoped to one cluster's id, because a webhook and a scan cadence belong to
// that cluster, not to the agent process.
export const getSystemIntegrations = (id) => getJSON(`/api/systems/${id}/integrations`);
export const updateSystemIntegrations = (id, values) => putJSON(`/api/systems/${id}/integrations`, { values });
export const testSystemIntegrations = (id, target = 'teams') => postJSON(`/api/systems/${id}/integrations/test`, { target });
export const notifyIntegrations = (id, payload) => postJSON(`/api/systems/${id}/integrations/notify`, { payload });

// Auth endpoints
export const login = (username, password) => postJSON('/api/auth/login', { username, password });
export const changePassword = (oldPassword, newPassword) => putJSON('/api/auth/password', { oldPassword, newPassword });

// Admin endpoints
export const getUsers = () => getJSON('/api/admin/users');
export const createUser = (username, password, role, systems) => postJSON('/api/admin/users', { username, password, role, systems });
export const updateUserSystems = (id, systems) => putJSON(`/api/admin/users/${id}/systems`, { systems });
export const deleteUser = (id) => deleteJSON(`/api/admin/users/${id}`);
export const getRegisteredSystems = () => getJSON('/api/admin/systems');
export const registerSystem = (name) => postJSON('/api/admin/systems', { name });
export const deleteSystem = (id) => deleteJSON(`/api/admin/systems/${id}`);
