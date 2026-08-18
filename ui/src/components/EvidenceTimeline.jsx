import { useState } from 'react';
import { usePreferences } from '../preferences';
import { useInvestigation } from '../InvestigationContext';
import { getLogsContext } from '../api';
import { useToast } from '../toast';

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
  const { request } = useInvestigation();
  const [onlyNotable, setOnlyNotable] = useState(false);
  const [kinds, setKinds] = useState({ log: true, event: true, metric: true });
  const [collapsed, setCollapsed] = useState(false);
  const systemId = request?.system_id;

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
            {visible.map((entry) => <Row key={entry.id} entry={entry} short={formatClock} systemId={systemId} />)}
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

function Row({ entry, short, systemId }) {
  const [open, setOpen] = useState(false);
  const [contextLogs, setContextLogs] = useState(null);
  const [loadingContext, setLoadingContext] = useState(false);
  const toast = useToast();
  const level = (entry.level || '').toLowerCase();

  const handleLoadContext = async () => {
    if (contextLogs) {
       setContextLogs(null);
       return;
    }
    setLoadingContext(true);
    try {
      const ts = Math.floor(new Date(entry.first_seen).getTime() / 1000);
      const logs = await getLogsContext(systemId, ts, entry.service);
      setContextLogs(logs);
      setOpen(true);
    } catch (err) {
      toast.error('Could not load context logs', { detail: err.message });
    } finally {
      setLoadingContext(false);
    }
  };

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
          {entry.kind === 'log' && systemId && (
            <button type="button" className="btn btn--ghost btn--sm" style={{ padding: '0 6px', fontSize: '11px', height: '20px' }} onClick={handleLoadContext} disabled={loadingContext}>
              {loadingContext ? '...' : contextLogs ? 'Hide context' : 'View surrounding context'}
            </button>
          )}
          <button type="button" className="li-evtl-id" onClick={() => setOpen(!open)}>
            {entry.id}
          </button>
        </div>

        {entry.notable && entry.notable_reason && (
          <div className="li-evtl-why">{entry.notable_reason}</div>
        )}
        {open && entry.detail && <pre className="li-raw-pre">{entry.detail}</pre>}
        {contextLogs && (
          <div className="context-logs" style={{ marginTop: '10px', padding: '12px', background: 'var(--bg-inset)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)' }}>
            <h4 style={{ margin: '0 0 12px 0', fontSize: '12px', color: 'var(--text-3)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Surrounding Logs</h4>
            <div style={{ fontFamily: 'var(--mono)', fontSize: '12px', color: 'var(--text-2)' }}>
              {contextLogs.length === 0 ? 'No surrounding logs found.' : contextLogs.map((log) => (
                <div key={log.id} style={{ display: 'flex', gap: '12px', borderBottom: '1px solid var(--border-soft)', padding: '6px 0', wordBreak: 'break-all' }}>
                  <span style={{ color: 'var(--text-4)', flexShrink: 0 }}>{new Date(log.timestamp).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
                  <span className={`li-evtl-badge li-evtl-badge--${(log.level||'').toLowerCase()}`} style={{ flexShrink: 0, width: '50px', textAlign: 'center' }}>{log.level || 'INFO'}</span>
                  <span style={{ flex: 1 }}>{log.message}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </li>
  );
}

function spanLabel(entry, short) {
  if (!entry.last_seen) return `${entry.occurrences} occurrences`;
  return `${entry.occurrences} occurrences between ${short(entry.first_seen)} and ${short(entry.last_seen)}`;
}


