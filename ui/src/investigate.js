/**
 * Builds the react-router nav state that hands the agent a question and a
 * time window, from two places the dashboard lets a reader point at a moment
 * rather than type one: a detection (a coloured dot on the anomaly timeline)
 * and a point on a chart (a click inside the fullscreen chart view).
 *
 * Both funnel into the same place AlertsPanel's "Investigate with agent"
 * already does — `navigate('/agent', { state })`, which AgentPage reads for
 * `question`/`start_time`/`end_time` and starts the run immediately. That is
 * what makes this "auto scan": the click *is* the start, there is no
 * intermediate form to fill in.
 *
 * The two builders stay separate rather than sharing one shape. A detection
 * already carries the window that matters — when it fired — and only needs
 * padding. A chart click is just a timestamp with no window of its own, so
 * one has to be invented, and how much depends on how zoomed-in the chart
 * already was: a point picked out of a 7-day view needs a wider bracket than
 * one picked out of a 1-hour view. Forcing both through one function would
 * mean a padding formula that fits neither case well.
 */

// Same lead-in AlertsPanel uses for a detection: enough room for the onset
// detector (`app/pipeline/windows.py`) to find what led up to it, without
// reaching so far back that an unrelated earlier incident enters the window.
const DETECTION_LEAD_IN_MS = 30 * 60 * 1000;

/** `{detected_at, timestamp}` fields, rendered in the reader's own zone. */
function formatDetectionPayload(payload, formatStamp) {
  if (!payload) return payload;
  const formatted = { ...payload };
  if (formatted.detected_at) formatted.detected_at = formatStamp(formatted.detected_at);
  if (formatted.timestamp) formatted.timestamp = formatStamp(formatted.timestamp);
  return formatted;
}

/**
 * Nav state for a detection clicked on the dashboard's anomaly timeline.
 *
 * `alert` is the *raw* shape `/api/systems/{id}/alerts` returns — monitor_name,
 * trigger_name, state, severity, start_time, end_time, error_message, service —
 * which is what the timeline is handed directly, unmapped. Deliberately not
 * built from AlertsPanel's own mapped alert shape: that panel renames several
 * of these fields for its own rendering, and reading its output here would
 * make the two places one field rename away from silently disagreeing about
 * which window gets investigated.
 */
export function detectionNavState(alert, system, formatStamp, { auto = false } = {}) {
  const firedAt = Number(alert.start_time || alert.timestamp) || Date.now();
  // A still-open detection is analysed up to now, not to whenever it was last
  // polled — the same reasoning AlertsPanel's investigate() uses.
  const endedAt = alert.state === 'COMPLETED' && alert.end_time
    ? Number(alert.end_time) : Date.now();
  const service = alert.service || 'cluster-wide';
  const title = alert.monitor_name || alert.title || 'Detection';
  const payload = {
    trigger: alert.trigger_name,
    state: alert.state,
    details: alert.error_message || 'Anomaly Detected',
  };

  return {
    system_id: system.id,
    environment: system.environments?.[0],
    service,
    question: `${title} on ${service}. Investigate the likely root cause.\n\n`
      + `The detection fired at ${formatStamp(firedAt)}.\n\n`
      + `Detection payload:\n${JSON.stringify(formatDetectionPayload(payload, formatStamp), null, 2)}`,
    start_time: new Date(firedAt - DETECTION_LEAD_IN_MS).toISOString(),
    end_time: new Date(Math.max(endedAt, firedAt + 60000)).toISOString(),
    kind: 'alert',
    label: title,
    serviceLabel: service,
    alertId: alert.id,
    auto,
  };
}

// Flat, not proportional to how zoomed-in the chart was — that was tried
// first (10% of the visible span) and broke on a 24h view: a click at 14:15
// padded forward to 16:39, which landed at "now", and the onset detector
// correctly did what it is designed to do when a range reaches to now — it
// reported the live incident happening THEN instead of anything near 14:15.
// The click's own precision does not scale with the chart's zoom level the
// way that formula assumed, so a fixed bracket avoids ever reaching far
// enough to catch an unrelated later incident by accident.
const CHART_PAD_MS = 20 * 60 * 1000;

/**
 * Nav state for a point clicked inside the fullscreen chart view.
 *
 * The bracket sent is not itself the search window: the agent's own onset
 * detection looks further back than this to find where the departure began
 * and where a quiet baseline sits (`WindowResolver.resolve` in
 * `app/pipeline/windows.py`), so this only needs to reach the moment
 * clicked — not to already be the answer.
 */
export function chartPointNavState({ system, title, timestampMs, serviceHint, formatStamp }) {
  const start = timestampMs - CHART_PAD_MS;
  // A point clicked near the right edge of a live chart should not ask about
  // the future; a still-open incident is better served by running to now.
  const end = Math.min(Date.now(), timestampMs + CHART_PAD_MS);
  const when = formatStamp(timestampMs);

  return {
    system_id: system.id,
    environment: system.environments?.[0],
    service: serviceHint,
    question: `${title} showed a notable change around ${when}. Investigate what `
      + `happened around this point and identify the root cause if there is one.`,
    start_time: new Date(start).toISOString(),
    end_time: new Date(end).toISOString(),
    kind: 'chart',
    label: `${title} @ ${when}`,
    serviceLabel: serviceHint,
  };
}
