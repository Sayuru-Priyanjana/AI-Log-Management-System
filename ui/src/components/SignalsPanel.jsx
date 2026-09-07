import Collapsible from './Collapsible';

/**
 * The measured departures from baseline, as a readable list.
 *
 * These were previously a row of bare type chips whose only detail lived in a
 * `title` attribute — so the one part of the investigation that is *measured*
 * rather than written by a model was the least legible thing on the page, and
 * on a touch screen it had no detail at all.
 *
 * Every signal carries what it is, where, how far it moved and when it started.
 * That is what makes the answer checkable: the model chose among these, so
 * reading them is how you tell whether it chose well.
 */
const SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };

export default function SignalsPanel({ signals, formatClock }) {
  const list = signals || [];

  if (!list.length) {
    return (
      <Collapsible title="Signal detection" summary="nothing crossed a threshold">
        <p className="li-empty">
          No measurement departed from its baseline by enough to be reported. That is a
          result, not a failure to look — the same thresholds that would have fired did
          run over this window.
        </p>
      </Collapsible>
    );
  }

  const sorted = [...list].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9),
  );
  const bySeverity = sorted.reduce((acc, s) => {
    acc[s.severity] = (acc[s.severity] || 0) + 1;
    return acc;
  }, {});
  const summary = Object.entries(bySeverity)
    .map(([sev, n]) => `${n} ${sev}`).join(' · ');

  return (
    <Collapsible title="Signal detection"
      summary={`${list.length} signal${list.length === 1 ? '' : 's'} · ${summary}`}
      right={<SeverityDots counts={bySeverity} />}>
      <ul className="li-signals">
        {sorted.map((signal) => (
          <li key={signal.id} className={`li-signal li-signal--${signal.severity}`}>
            <div className="li-signal-top">
              <span className={`li-chip li-chip--${signal.severity}`}>{signal.type}</span>
              {signal.service && (
                <span className="li-chip li-chip--service">{signal.service}</span>
              )}
              <span className="li-spacer" />
              {signal.onset && formatClock && (
                <span className="li-muted li-signal-onset">began {formatClock(signal.onset)}</span>
              )}
            </div>

            <p className="li-signal-desc">{signal.description}</p>

            {/* The comparison, not just the value. "180 errors/min" means
                nothing without the baseline it is being judged against. */}
            {signal.magnitude && (
              <div className="li-signal-mag">
                <span className="li-signal-mag-val">
                  {fmt(signal.magnitude.incident)} {signal.magnitude.unit}
                </span>
                {signal.magnitude.baseline !== undefined && signal.magnitude.baseline !== null && (
                  <>
                    <span className="li-muted"> vs </span>
                    <span className="li-signal-mag-base">
                      {fmt(signal.magnitude.baseline)} {signal.magnitude.unit} at baseline
                    </span>
                  </>
                )}
                {ratio(signal.magnitude) && (
                  <span className="li-signal-ratio">{ratio(signal.magnitude)}</span>
                )}
              </div>
            )}

            <code className="li-signal-id">{signal.id}</code>
          </li>
        ))}
      </ul>
    </Collapsible>
  );
}

function SeverityDots({ counts }) {
  const order = ['critical', 'high', 'medium', 'low', 'info'];
  return (
    <span className="li-signal-dots" aria-hidden="true">
      {order.filter((s) => counts[s]).map((s) => (
        <span key={s} className={`li-signal-dot li-signal-dot--${s}`} title={`${counts[s]} ${s}`} />
      ))}
    </span>
  );
}

function fmt(value) {
  if (value === null || value === undefined) return '—';
  if (typeof value !== 'number') return String(value);
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toFixed(value < 10 ? 2 : 1);
}

function ratio(magnitude) {
  const { incident, baseline } = magnitude;
  if (typeof incident !== 'number' || typeof baseline !== 'number') return null;
  if (baseline <= 0) return incident > 0 ? 'new' : null;
  const factor = incident / baseline;
  if (factor >= 1.1) return `${factor.toFixed(1)}× higher`;
  if (factor <= 0.9) return `${(1 / factor).toFixed(1)}× lower`;
  return null;
}
