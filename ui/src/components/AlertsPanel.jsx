import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { usePreferences } from '../preferences';
import { useToast } from '../toast';
import { getSystemIntegrations, notifyIntegrations, getSystemAlerts } from '../api';
import { alertCard } from '../teams';
import { useInvestigation } from '../InvestigationContext';
import { getAlertStatuses, setAlertStatus } from '../mockData';

const STATUSES = ['pending', 'investigating', 'handled'];
const SEVERITY_TONE = { high: 'err', medium: 'warn', low: '' };

/**
 * OpenSearch alerts and detections, as cards.
 */
export default function AlertsPanel({ system }) {
  const navigate = useNavigate();
  const toast = useToast();
  const { formatClock, formatDay, formatStamp } = usePreferences();
  const [alerts, setAlerts] = useState([]);
  const [open, setOpen] = useState(null);
  const [scanning, setScanning] = useState(false);
  // Which alert ids have already been posted, so a detection is announced once
  // and not re-announced every 15 seconds by the refresh below.
  const notified = useRef(new Set());
  // The first load is a snapshot of what already exists, not news. Without this
  // opening the page posts every standing alert to the channel at once.
  const seeded = useRef(false);
  const { startInvestigation, setRequest, setMeta, setStatus } = useInvestigation();

  const fetchAlerts = async () => {
    try {
      const data = await getSystemAlerts(system.id);
      // Triage state is ours, not OpenSearch's — the alerting plugin has no
      // field for "someone looked at this". Re-reading it here is what stops
      // the fifteen-second refresh below resetting every card to `pending`,
      // including the one the agent just finished investigating.
      const statuses = getAlertStatuses();
      const mappedAlerts = data.map(a => ({
        id: a.id,
        title: a.monitor_name,
        service: a.service || 'cluster-wide',
        severity: a.severity === "1" ? 'high' : 'medium',
        timestamp: a.start_time || new Date().getTime(),
        status: statuses[a.id]?.status || 'pending',
        endTime: a.end_time,
        payload: {
          trigger: a.trigger_name,
          state: a.state,
          details: a.error_message || "Anomaly Detected"
        }
      }));
      setAlerts(mappedAlerts);
      announce(mappedAlerts);
    } catch (err) {
      console.error("Failed to fetch alerts", err);
    }
  };

  /**
   * Posts newly seen detections to the channel.
   *
   * `notify_on_alert_enabled` has existed in the settings — and in the settings
   * UI — with nothing behind it: turning it on did nothing at all. This is the
   * sender. It is deliberately quiet about its own failures: a webhook that is
   * misconfigured should not bury the alert list under error toasts every
   * refresh cycle.
   */
  const announce = async (list) => {
    if (!seeded.current) {
      seeded.current = true;
      list.forEach((a) => notified.current.add(a.id));
      return;
    }
    const fresh = list.filter((a) => !notified.current.has(a.id));
    if (!fresh.length) return;
    // Marked before sending, so a slow webhook cannot be posted to twice by the
    // next refresh landing mid-flight.
    fresh.forEach((a) => notified.current.add(a.id));
    try {
      const { values } = await getSystemIntegrations(system.id);
      if (!values?.notify_on_alert_enabled) return;
      for (const alert of fresh) {
        await notifyIntegrations(system.id,
          alertCard({ alert, systemName: system.name, systemId: system.id, formatStamp }));
      }
    } catch (err) {
      console.warn('Could not post alerts to integrations:', err.message);
    }
  };

  useEffect(() => {
    seeded.current = false;
    notified.current = new Set();
    fetchAlerts(); 
    
    // Auto-refresh alerts every 15 seconds
    const interval = setInterval(() => {
      fetchAlerts();
    }, 15000);
    
    return () => clearInterval(interval);
  }, [system.id]);

  const formatPayload = (payload) => {
    if (!payload) return payload;
    const formatted = { ...payload };
    if (formatted.detected_at) formatted.detected_at = formatStamp(formatted.detected_at);
    if (formatted.timestamp) formatted.timestamp = formatStamp(formatted.timestamp);
    return formatted;
  };

  const scan = () => {
    setScanning(true);
    setTimeout(async () => {
      await fetchAlerts();
      setScanning(false);
      toast.info(`Scanned OpenSearch for new alerts`);
    }, 500);
  };

  const changeStatus = (id, status) => {
    setAlertStatus(id, status);
    setAlerts((prev) => prev.map((a) => (a.id === id ? { ...a, status } : a)));
    setOpen((prev) => (prev?.id === id ? { ...prev, status } : prev));
  };

  const removeAlert = (id, e) => {
    if (e) e.stopPropagation();
    setAlerts((prev) => prev.filter(a => a.id !== id));
    if (open && open.id === id) setOpen(null);
  };

  const investigate = (alert, auto = false) => {
    changeStatus(alert.id, 'investigating');
    setOpen(null);
    
    // The window the alert is about, not the default lookback.
    //
    // A detection carries the moment it fired, and handing the agent only a
    // question meant the run used whatever default window the form would have
    // used. An alert that opened three hours ago was then investigated over the
    // last hour — a period in which nothing had happened — and came back
    // all-clear on an alert that was still open.
    //
    // The window starts a little before the alert so the onset detector has room
    // to find what led up to it, and runs to now rather than to the alert's end
    // so a still-open detection is analysed up to the present. Both ends are
    // ISO-8601 UTC, which is what the planner parses.
    const firedAt = Number(alert.timestamp) || Date.now();
    const LEAD_IN_MS = 30 * 60 * 1000;
    const endedAt = alert.payload?.state === 'COMPLETED' && alert.endTime
      ? Number(alert.endTime) : Date.now();

    const navState = {
      system_id: system.id,
      environment: system.environments?.[0],
      service: alert.service,
      question: `${alert.title} on ${alert.service}. Investigate the likely root cause.\n\n`
        + `The detection fired at ${formatStamp(firedAt)}.\n\n`
        + `Detection payload:\n${JSON.stringify(formatPayload(alert.payload), null, 2)}`,
      start_time: new Date(firedAt - LEAD_IN_MS).toISOString(),
      end_time: new Date(Math.max(endedAt, firedAt + 60000)).toISOString(),
      kind: 'alert',
      label: alert.title,
      serviceLabel: alert.service,
      alertId: alert.id,
      auto: auto,
    };

    if (auto) {
      toast.info(`Auto-investigating: ${alert.title}`);
    }
    
    navigate('/agent', { state: navState });
  };

  return (
    <div className="card card--fill">
      <header>
        <h3>Alerts &amp; detections</h3>
        <span className="spacer" />
        <span className="dim">{alerts.length}</span>
        <button type="button" className="btn btn--sm" disabled={scanning} onClick={scan}>
          {scanning ? 'Scanning…' : 'Scan'}
        </button>
      </header>
      <div className="card-body">
        {alerts.length === 0 && (
          <div className="empty-state">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
            </svg>
            <div className="empty-state-title">No active alerts</div>
            <div className="empty-state-desc">No detections yet. Scan checks OpenSearch for new ones.</div>
          </div>
        )}
        {alerts.map((alert) => (
          <div key={alert.id} className="alert-card" role="button" tabIndex={0} onClick={() => setOpen(alert)} onKeyDown={(e) => { if(e.key==='Enter') setOpen(alert); }}>
            <div className="alert-card-head">
              <span className={`chip chip--${SEVERITY_TONE[alert.severity] || ''}`}>{alert.severity}</span>
              <span className="alert-card-title">{alert.title}</span>
            </div>
            <div className="alert-card-meta">
              <span className="mono">{alert.service}</span>
              <span>{formatDay(alert.timestamp)} {formatClock(alert.timestamp)}</span>
              <span className="spacer" />
              <select className="select-sm" value={alert.status}
                onClick={(e) => e.stopPropagation()}
                onChange={(e) => changeStatus(alert.id, e.target.value)}>
                {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
              <button type="button" className="iconbtn" onClick={(e) => removeAlert(alert.id, e)} title="Delete alert">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="3 6 5 6 21 6"></polyline>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                </svg>
              </button>
            </div>
          </div>
        ))}
      </div>

      {open && (
        <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && setOpen(null)}>
          <div className="modal modal--wide" role="dialog" aria-modal="true">
            <header>
              <h3>{open.title}</h3>
              <span className="spacer" />
              <button type="button" className="iconbtn" onClick={() => removeAlert(open.id)} title="Delete alert" style={{marginRight: '8px'}}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="3 6 5 6 21 6"></polyline>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                </svg>
              </button>
              <button type="button" className="iconbtn" onClick={() => setOpen(null)} aria-label="Close">×</button>
            </header>
            <div className="modal-body alert-detail">
              <dl className="kv-list">
                <div className="kv-row"><dt>Service</dt><dd className="mono">{open.service}</dd></div>
                <div className="kv-row"><dt>Severity</dt>
                  <dd><span className={`chip chip--${SEVERITY_TONE[open.severity] || ''}`}>{open.severity}</span></dd></div>
                <div className="kv-row"><dt>Detected</dt><dd>{formatDay(open.timestamp)} {formatClock(open.timestamp)}</dd></div>
                <div className="kv-row"><dt>Status</dt>
                  <dd>
                    <select className="select-sm" value={open.status}
                      onChange={(e) => changeStatus(open.id, e.target.value)}>
                      {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </dd>
                </div>
              </dl>
              <div>
                <span className="hint">Detection payload</span>
                <pre className="alert-payload">{JSON.stringify(formatPayload(open.payload), null, 2)}</pre>
              </div>
            </div>
            <div className="modal-foot">
              <button type="button" className="btn" onClick={() => setOpen(null)}>Close</button>
              <button type="button" className="btn btn--primary" onClick={() => investigate(open)}>
                Investigate with agent
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
