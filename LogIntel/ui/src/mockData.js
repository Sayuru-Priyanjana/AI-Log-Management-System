// Standing alert detections and their status history.
//
// There is no alerting engine behind this yet — no monitor/rule store in
// OpenSearch, no scheduler to run one. Real infrastructure for that is a
// project on its own (rule definitions, a background evaluator, dedup against
// the same detection firing every cycle). Until it exists, this module is an
// honest stand-in: it persists, "Scan" produces a plausible new detection drawn
// from the system's actual services rather than generic filler, and clicking
// "Investigate with agent" on one starts a genuine investigation through the
// real pipeline — the data is fake, the action it triggers is not.
const ALERTS_KEY = 'logintel.mock.alerts';
const ACTIVITIES_KEY = 'logintel.mock.activities';

const TEMPLATES = [
  { title: 'Error rate spike', severity: 'high',
    summary: (svc) => `${svc} error rate rose sharply above its baseline.` },
  { title: 'Latency degradation', severity: 'medium',
    summary: (svc) => `${svc} p95 latency has been elevated for several consecutive buckets.` },
  { title: 'Pod restart loop', severity: 'high',
    summary: (svc) => `A pod backing ${svc} has restarted repeatedly in the last 15 minutes.` },
  { title: 'Traffic surge', severity: 'low',
    summary: (svc) => `${svc} request rate is several times its usual level.` },
  { title: 'Dependency unavailable', severity: 'high',
    summary: (svc) => `${svc} is failing to reach a downstream dependency.` },
];

let seq = 0;
const nextId = (prefix) => { seq += 1; return `${prefix}-${Date.now().toString(36)}-${seq}`; };

function readAll(key) {
  try { return JSON.parse(localStorage.getItem(key) || '[]'); } catch { return []; }
}
function writeAll(key, list) {
  localStorage.setItem(key, JSON.stringify(list.slice(0, 300)));
}

// -- alerts / detections -----------------------------------------------------
export function getAlerts(systemId) {
  return readAll(ALERTS_KEY)
    .filter((a) => a.system_id === systemId)
    .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
}

export function scanForAlerts(systemId, services) {
  const names = services?.length ? services.map((s) => s.name) : ['the system'];
  const template = TEMPLATES[Math.floor(Math.random() * TEMPLATES.length)];
  const service = names[Math.floor(Math.random() * names.length)];
  const now = new Date().toISOString();
  const alert = {
    id: nextId('alert'),
    system_id: systemId,
    title: template.title,
    service,
    severity: template.severity,
    timestamp: now,
    status: 'pending',
    payload: {
      source: 'opensearch-detection (simulated — no alerting engine wired up yet)',
      rule: template.title.toLowerCase().replace(/ /g, '_'),
      system_id: systemId,
      service,
      severity: template.severity,
      window: '15m',
      detected_at: now,
      summary: template.summary(service),
    },
  };
  writeAll(ALERTS_KEY, [alert, ...readAll(ALERTS_KEY)]);
  logActivity(systemId, {
    kind: 'automated',
    label: `Scan detected: ${template.title} — ${service}`,
    status: 'done',
  });
  return alert;
}

export function setAlertStatus(id, status) {
  writeAll(ALERTS_KEY, readAll(ALERTS_KEY).map((a) => (a.id === id ? { ...a, status } : a)));
}

export function deleteAlert(id) {
  writeAll(ALERTS_KEY, readAll(ALERTS_KEY).filter((a) => a.id !== id));
}

// -- activities ---------------------------------------------------------------
export function getAutomatedActivities(systemId, sinceMs) {
  const cutoff = sinceMs ? Date.now() - sinceMs : 0;
  return readAll(ACTIVITIES_KEY)
    .filter((a) => a.system_id === systemId && new Date(a.timestamp).getTime() >= cutoff)
    .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
}

export function logActivity(systemId, { kind, label, status }) {
  const entry = { id: nextId('act'), system_id: systemId, kind, label, status,
                  timestamp: new Date().toISOString() };
  writeAll(ACTIVITIES_KEY, [entry, ...readAll(ACTIVITIES_KEY)]);
  return entry;
}
