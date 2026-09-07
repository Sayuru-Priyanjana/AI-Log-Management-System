/**
 * The messages LogIntel posts to a Teams channel.
 *
 * One module, because the card these produce is the only thing most of the team
 * will ever see of an investigation. Previously the single message that existed
 * was assembled inline: it guessed severity by searching the prose for the words
 * "root cause", titled itself "Agent Result: Investigation", and pasted the
 * headline and detail into one block with no window, no service, no confidence
 * and no evidence — so a reader in the channel could not tell what was examined,
 * how sure the agent was, or whether it was even the right system.
 *
 * Three rules hold for everything here:
 *
 *  - severity comes from the data, never from the wording. `incident_detected`,
 *    the signal severities and the cause category are all measured; matching on
 *    the phrase "root cause" mislabels "no root cause was found" as an incident.
 *  - the card carries the facts a reader needs to act without opening the app:
 *    which system, which service, when, how confident, and what it rests on.
 *  - MessageCard is strict. It silently drops a message containing HTML, and it
 *    renders code fences badly, so every string goes through `plain()`.
 */

const THEME = {
  critical: 'D13438',
  high: 'E81123',
  medium: 'F7A501',
  low: '0078D4',
  none: '107C10',
  info: '5B5FC7',
};

const SEVERITY_LABEL = {
  critical: '🔴 Critical', high: '🔴 High', medium: '🟠 Medium',
  low: '🔵 Low', none: '🟢 Healthy', info: '🔵 Info',
};

/**
 * Teams drops a MessageCard containing raw HTML and mangles code fences, so
 * text is flattened before it is sent. Truncation is marked rather than silent:
 * a reader who cannot tell an answer was cut will act on half of it.
 */
export function plain(text, limit = 1400) {
  if (!text) return '';
  let out = String(text)
    .replace(/```[a-z]*\n?([\s\S]*?)```/gi, '\n$1\n')
    .replace(/`/g, '')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  if (out.length > limit) out = `${out.slice(0, limit).trimEnd()}…\n\n_(truncated — open LogIntel for the full answer)_`;
  return out;
}

function fact(name, value) {
  return value === undefined || value === null || value === '' ? null : { name, value: String(value) };
}

function card({ title, summary, severity = 'info', facts = [], sections = [] }) {
  return {
    '@type': 'MessageCard',
    '@context': 'http://schema.org/extensions',
    summary: plain(summary || title, 120) || 'LogIntel notification',
    themeColor: THEME[severity] || THEME.info,
    title,
    sections: [
      { facts: facts.filter(Boolean), markdown: true },
      ...sections.filter(Boolean),
    ],
  };
}

/**
 * The result of an investigation.
 *
 * Severity is read from the analysis, not inferred from the prose. An
 * investigation that found nothing is a green card that says so plainly —
 * that is a useful message, and the previous version rendered it identically
 * to a real incident whenever the words "root cause" appeared anywhere.
 */
export function investigationCard({ result, systemName, systemId, label, formatStamp }) {
  const answer = result?.answer || {};
  const analysis = result?.analysis || {};
  const signals = result?.signals || [];
  const windows = result?.windows || {};

  const detected = analysis.incident_detected;
  const severity = detected ? (analysis.severity || 'high') : 'none';
  const confidence = typeof answer.confidence === 'number'
    ? `${Math.round(answer.confidence * 100)}%` : null;

  const window = windows.incident?.start && windows.incident?.end && formatStamp
    ? `${formatStamp(windows.incident.start)} → ${formatStamp(windows.incident.end)}`
    : null;

  // The top few signals, named. "3 signals detected" tells a reader nothing they
  // can act on; "OOM_KILL on payment-db" tells them where to look.
  const topSignals = signals.slice(0, 4)
    .map((s) => `- **${s.type}**${s.service ? ` on ${s.service}` : ''}${s.description ? ` — ${plain(s.description, 160)}` : ''}`)
    .join('\n');

  const steps = (answer.next_steps || []).slice(0, 4)
    .map((s) => `- ${plain(typeof s === 'string' ? s : s.label, 160)}`)
    .join('\n');

  return card({
    title: detected
      ? `🔎 Incident: ${plain(answer.headline || 'Investigation complete', 150)}`
      : `✅ ${plain(answer.headline || 'No anomalies found', 150)}`,
    summary: answer.headline || 'LogIntel investigation result',
    severity,
    facts: [
      fact('Status', SEVERITY_LABEL[severity] || SEVERITY_LABEL.info),
      fact('System', systemName || systemId),
      fact('Service', answer.root_cause_service),
      fact('Window analysed', window),
      fact('Confidence', confidence),
      fact('Signals detected', signals.length || '0'),
      fact('Trigger', label || 'Manual investigation'),
      fact('Investigation', result?.id),
    ],
    sections: [
      answer.detail && {
        activityTitle: '**What happened**',
        text: plain(answer.detail),
        markdown: true,
      },
      topSignals && {
        activityTitle: `**Measured signals** (${signals.length})`,
        text: topSignals + (signals.length > 4 ? `\n- _…and ${signals.length - 4} more_` : ''),
        markdown: true,
      },
      steps && {
        activityTitle: '**Suggested next steps**',
        text: steps,
        markdown: true,
      },
      (answer.limitations || []).length > 0 && {
        activityTitle: '**What this does not establish**',
        text: answer.limitations.slice(0, 3).map((l) => `- ${plain(l, 200)}`).join('\n'),
        markdown: true,
      },
    ],
  });
}

/**
 * A detection that fired, posted when it is raised rather than after anyone
 * investigates it. This is the message a channel is actually watching for.
 */
export function alertCard({ alert, systemName, systemId, formatStamp }) {
  const severity = alert.severity === 'high' ? 'high'
    : alert.severity === 'medium' ? 'medium' : 'low';
  const payload = alert.payload || {};

  return card({
    title: `🚨 Alert: ${plain(alert.title || 'Detection fired', 150)}`,
    summary: `Alert on ${alert.service || systemName || systemId}`,
    severity,
    facts: [
      fact('Severity', SEVERITY_LABEL[severity]),
      fact('System', systemName || systemId),
      fact('Service', alert.service),
      fact('Fired at', alert.timestamp && formatStamp ? formatStamp(alert.timestamp) : null),
      fact('Monitor', alert.title),
      fact('Trigger', payload.trigger),
      fact('State', payload.state),
    ],
    sections: [
      payload.details && {
        activityTitle: '**What fired**',
        text: plain(payload.details, 800),
        markdown: true,
      },
      {
        text: '_Open LogIntel and use “Investigate with agent” to get a root-cause analysis for this detection._',
        markdown: true,
      },
    ],
  });
}
