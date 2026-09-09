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

let seq = 0;
const nextId = (prefix) => { seq += 1; return `${prefix}-${Date.now().toString(36)}-${seq}`; };

function readAll(key) {
  try { return JSON.parse(localStorage.getItem(key) || '[]'); } catch { return []; }
}
function writeAll(key, list) {
  localStorage.setItem(key, JSON.stringify(list.slice(0, 300)));
}

// -- alerts / detections -----------------------------------------------------
// Alert generation logic was replaced by OpenSearch anomaly detection integration.


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

// -- alert status -------------------------------------------------------------
// Which detections have been triaged, and how far.
//
// The alerts themselves come from OpenSearch now — the generator that used to
// live here is gone — but their *status* does not: OpenSearch alerting has no
// field for "a human looked at this", and inventing one would mean writing back
// into an index the alerting plugin owns. So triage state stays here, keyed by
// the alert's own document id, and survives the fifteen-second refresh that
// otherwise rebuilds every card from scratch.
//
// This also has to exist because two call sites already used it. `setAlertStatus`
// was removed along with the generator while `AgentPage` still imported it, so
// finishing an alert investigation threw "setAlertStatus is not a function" every
// time — the one path in the app where an error was guaranteed rather than
// possible.
const ALERT_STATUS_KEY = 'logintel.alertStatus';

function readStatuses() {
  try { return JSON.parse(localStorage.getItem(ALERT_STATUS_KEY) || '{}'); } catch { return {}; }
}

export function getAlertStatuses() {
  return readStatuses();
}

export function getAlertStatus(id, fallback = 'pending') {
  return readStatuses()[id]?.status || fallback;
}

export function setAlertStatus(id, status) {
  if (!id) return null;
  const all = readStatuses();
  all[id] = { status, at: new Date().toISOString() };
  // Bounded: an alerting system that fires all day would otherwise grow this
  // without limit in a tab nobody reloads.
  const ids = Object.keys(all);
  if (ids.length > 500) {
    for (const stale of ids.slice(0, ids.length - 500)) delete all[stale];
  }
  try { localStorage.setItem(ALERT_STATUS_KEY, JSON.stringify(all)); } catch { /* quota */ }
  return all[id];
}
