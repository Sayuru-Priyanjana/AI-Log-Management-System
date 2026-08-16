import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { usePreferences } from '../preferences';
import { getAutomatedActivities } from '../mockData';

const WINDOWS = [
  { value: 1, label: 'Last 1h' },
  { value: 6, label: 'Last 6h' },
  { value: 24, label: 'Last 24h' },
  { value: 48, label: 'Last 2 days' },
  { value: 168, label: 'Last 7 days' },
  { value: 0, label: 'All time' },
];

/**
 * What happened, merged from two sources.
 *
 * "User" rows are real: every investigation actually run against this system,
 * from the same history the Recent Chats list reads. "Automated" rows are not —
 * there is no scheduler behind them yet, only the simulated scan on the alerts
 * column — and they carry an `auto` tag rather than pretending otherwise.
 */
export default function ActivitiesPanel({ systemId, investigations }) {
  const navigate = useNavigate();
  const { formatClock, formatDay } = usePreferences();
  const [windowHours, setWindowHours] = useState(24);

  const rows = useMemo(() => {
    const cutoff = windowHours ? Date.now() - windowHours * 3600_000 : 0;

    const user = (investigations || [])
      .filter((inv) => new Date(inv.created_at).getTime() >= cutoff)
      .map((inv) => ({
        id: inv.id,
        kind: 'user',
        label: inv.question || 'Investigation',
        status: activityStatus(inv),
        timestamp: inv.created_at,
      }));

    const automated = getAutomatedActivities(systemId, windowHours ? windowHours * 3600_000 : undefined)
      .map((a) => ({ ...a, kind: 'automated' }));

    return [...user, ...automated].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
  }, [investigations, systemId, windowHours]);

  const handleRowClick = (row) => {
    if (row.kind === 'user') {
      navigate('/agent', { state: { system_id: systemId, investigation_id: row.id } });
    }
  };

  return (
    <div className="card card--fill">
      <header>
        <h3>Activities</h3>
        <span className="spacer" />
        <select className="select-sm" value={windowHours}
          onChange={(e) => setWindowHours(Number(e.target.value))}>
          {WINDOWS.map((w) => <option key={w.value} value={w.value}>{w.label}</option>)}
        </select>
      </header>
      <div className="card-body">
        {rows.length === 0 && <div className="empty">Nothing in this window.</div>}
        {rows.map((row) => (
          <div 
            key={row.id} 
            className={`activity-row ${row.kind === 'user' ? 'is-clickable' : ''}`}
            onClick={() => handleRowClick(row)}
            style={row.kind === 'user' ? { cursor: 'pointer' } : {}}
          >
            <span className={`activity-tag activity-tag--${row.status}`} title={row.status} />
            <div className="activity-body">
              <div className="activity-label">{row.label}</div>
              <div className="activity-meta">
                <span className={`activity-kind ${row.kind === 'user' ? 'activity-kind--user' : ''}`}>
                  {row.kind === 'user' ? 'you' : 'auto'}
                </span>
                <span>{formatDay(row.timestamp)} {formatClock(row.timestamp)}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function activityStatus(investigation) {
  if (investigation.errors?.length) return 'failed';
  if (investigation.analysis?.confidence != null || investigation.analysis?.cause_summary) return 'done';
  return 'pending';
}
