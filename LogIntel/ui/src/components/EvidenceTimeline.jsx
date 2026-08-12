import { useState } from 'react';
import { usePreferences } from '../preferences';

/**
 * Everything the investigation looked at, in order.
 *
 * One row per distinct thing, never per occurrence: 451 identical
 * "DependencyUnreachable" lines are one row that happened 451 times, spanning
 * the period it covered. The folding is real — the log tool collapses messages
 * to templates and the event collector keeps Kubernetes' own repeat counts — so
 * this is the actual shape of the evidence, not a display trick.
 *
 * Notable rows are highlighted and can be isolated. Highlighting everything
 * would highlight nothing, so each one states why it earned it.
 */
const KIND_LABEL = { log: 'log', event: 'k8s', metric: 'metric', marker: '' };

export default function EvidenceTimeline({ data }) {
  const { formatClock, zoneLabel } = usePreferences();
  const [onlyNotable, setOnlyNotable] = useState(false);
  const [kinds, setKinds] = useState({ log: true, event: true, metric: true });
  const [collapsed, setCollapsed] = useState(false);

  const entries = data?.entries || [];
  if (!entries.length) return null;

  const visible = entries.filter(
    (e) => (e.kind === 'marker' || kinds[e.kind] !== false) && (!onlyNotable || e.notable),
  );
  const notableCount = entries.filter((e) => e.notable).length;
  const counts = entries.reduce((acc, e) => {
    if (e.kind !== 'marker') acc[e.kind] = (acc[e.kind] || 0) + 1;
    return acc;
  }, {});

  return (
    <div className="glass-panel li-evtl">
      <button type="button" className="li-trace-head" onClick={() => setCollapsed(!collapsed)}>
        <span className="li-trace-title">Evidence timeline</span>
        <span className="li-muted">
          {entries.length} distinct entries
          {data.collapsed_from ? ` folded from ${data.collapsed_from.toLocaleString()} documents` : ''}
          {notableCount ? ` · ${notableCount} notable` : ''}
        </span>
        <span className="li-spacer" />
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2.4"
          style={{ transform: collapsed ? 'none' : 'rotate(180deg)', transition: 'transform .2s' }}>
          <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {!collapsed && (
        <>
          <div className="li-evtl-controls">
            <button type="button"
              className={`li-filter ${onlyNotable ? 'li-filter--on' : ''}`}
              onClick={() => setOnlyNotable(!onlyNotable)}>
              Notable only
            </button>
            {['log', 'event', 'metric'].filter((k) => counts[k]).map((k) => (
              <button key={k} type="button"
                className={`li-filter ${kinds[k] ? 'li-filter--on' : ''}`}
                onClick={() => setKinds({ ...kinds, [k]: !kinds[k] })}>
                {KIND_LABEL[k]} ({counts[k]})
              </button>
            ))}
            <span className="li-spacer" />
            <span className="li-muted li-evtl-window">
              {formatClock(data.window?.start)} – {formatClock(data.window?.end)} {zoneLabel}
            </span>
          </div>

          <ol className="li-evtl-list">
            {visible.map((entry) => <Row key={entry.id} entry={entry} short={formatClock} />)}
          </ol>

          {visible.length === 0 && (
            <p className="li-empty" style={{ padding: '0 20px 18px' }}>
              Nothing matches the current filters.
            </p>
          )}
        </>
      )}
    </div>
  );
}

function Row({ entry, short }) {
  const [open, setOpen] = useState(false);
  const level = (entry.level || '').toLowerCase();

  return (
    <li className={`li-evtl-row li-evtl-row--${entry.kind}`
      + (entry.notable ? ' li-evtl-row--notable' : '')}>
      <time className="li-evtl-time">{short(entry.first_seen)}</time>

      <span className={`li-evtl-badge li-evtl-badge--${level || entry.kind}`}>
        {entry.level || KIND_LABEL[entry.kind]}
      </span>

      <div className="li-evtl-body">
        <div className="li-evtl-title">
          {entry.title}
          {entry.occurrences > 1 && (
            <span className="li-evtl-count" title={spanLabel(entry, short)}>
              ×{entry.occurrences.toLocaleString()}
            </span>
          )}
        </div>

        <div className="li-evtl-meta">
          {entry.service && <span className="li-evtl-service">{entry.service}</span>}
          {entry.occurrences > 1 && entry.last_seen && (
            <span>{short(entry.first_seen)} → {short(entry.last_seen)}</span>
          )}
          {typeof entry.baseline_occurrences === 'number' && (
            <span>
              baseline: {entry.baseline_occurrences === 0
                ? 'never seen'
                : `×${entry.baseline_occurrences.toLocaleString()}`}
            </span>
          )}
          <button type="button" className="li-evtl-id" onClick={() => setOpen(!open)}>
            {entry.id}
          </button>
        </div>

        {entry.notable && entry.notable_reason && (
          <div className="li-evtl-why">{entry.notable_reason}</div>
        )}
        {open && entry.detail && <pre className="li-raw-pre">{entry.detail}</pre>}
      </div>
    </li>
  );
}

function spanLabel(entry, short) {
  if (!entry.last_seen) return `${entry.occurrences} occurrences`;
  return `${entry.occurrences} occurrences between ${short(entry.first_seen)} and ${short(entry.last_seen)}`;
}


