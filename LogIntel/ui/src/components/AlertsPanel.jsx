import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { usePreferences } from '../preferences';
import { useToast } from '../toast';
import { getAlerts, scanForAlerts, setAlertStatus, deleteAlert } from '../mockData';
import { getSystemIntegrations, notifyIntegrations } from '../api';
import { useInvestigation } from '../InvestigationContext';

const STATUSES = ['pending', 'investigating', 'handled'];
const SEVERITY_TONE = { high: 'err', medium: 'warn', low: '' };

/**
 * OpenSearch alerts and detections, as cards.
 *
 * Standing behind a real detection engine — see mockData.js for why there
 * isn't one yet. What is real: clicking through to the agent starts an actual
 * investigation, seeded with this card's payload, against the real pipeline.
 */
export default function AlertsPanel({ system }) {
  const navigate = useNavigate();
  const toast = useToast();
  const { formatClock, formatStamp } = usePreferences();
  const [alerts, setAlerts] = useState([]);
  const [open, setOpen] = useState(null);
  const [scanning, setScanning] = useState(false);
  const { startInvestigation, setRequest, setMeta, setStatus } = useInvestigation();

  useEffect(() => { setAlerts(getAlerts(system.id)); }, [system.id]);

  const formatPayload = (payload) => {
    if (!payload) return payload;
    const formatted = { ...payload };
    if (formatted.detected_at) formatted.detected_at = formatStamp(formatted.detected_at);
    if (formatted.timestamp) formatted.timestamp = formatStamp(formatted.timestamp);
    return formatted;
  };

  const scan = () => {
    setScanning(true);
    // A moment of latency reads as a real check having happened, rather than a
    // card materialising the instant the button is pressed.
    setTimeout(async () => {
      const alert = scanForAlerts(system.id, system.services);
      setAlerts(getAlerts(system.id));
      setScanning(false);
      toast.info(`New detection: ${alert.title}`, { detail: alert.service });
      
      // Automation
      try {
        const { values } = await getSystemIntegrations(system.id);
        if (values.notify_on_alert_enabled) {
          const payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": SEVERITY_TONE[alert.severity] === 'err' ? 'FF0000' : 'FFA500',
            "summary": "New Alert",
            "title": `Alert: ${alert.title}`,
            "text": `**Service:** ${alert.service}\n\n**Severity:** ${alert.severity}\n\n${alert.payload.summary}`,
          };
          notifyIntegrations(system.id, payload).catch(err => console.error("Teams notification failed", err));
        }
        if (values.auto_investigate_alerts_enabled) {
          investigate(alert, true);
        }
      } catch (err) {
        console.error("Could not fetch integrations for automation", err);
      }
    }, 500);
  };

  const changeStatus = (id, status) => {
    setAlertStatus(id, status);
    setAlerts((prev) => prev.map((a) => (a.id === id ? { ...a, status } : a)));
    setOpen((prev) => (prev?.id === id ? { ...prev, status } : prev));
  };

  const removeAlert = (id, e) => {
    if (e) e.stopPropagation();
    deleteAlert(id);
    setAlerts(getAlerts(system.id));
    if (open && open.id === id) setOpen(null);
  };

  const investigate = (alert, auto = false) => {
    changeStatus(alert.id, 'investigating');
    setOpen(null);
    
    const navState = {
      system_id: system.id,
      environment: system.environments?.[0],
      service: alert.service,
      question: `${alert.title} on ${alert.service}. Investigate the likely root cause.\n\n`
        + `Detection payload:\n${JSON.stringify(formatPayload(alert.payload), null, 2)}`,
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
          <div className="empty">No detections yet. Scan checks OpenSearch for new ones.</div>
        )}
        {alerts.map((alert) => (
          <div key={alert.id} className="alert-card" role="button" tabIndex={0} onClick={() => setOpen(alert)} onKeyDown={(e) => { if(e.key==='Enter') setOpen(alert); }}>
            <div className="alert-card-head">
              <span className={`chip chip--${SEVERITY_TONE[alert.severity] || ''}`}>{alert.severity}</span>
              <span className="alert-card-title">{alert.title}</span>
            </div>
            <div className="alert-card-meta">
              <span className="mono">{alert.service}</span>
              <span>{formatClock(alert.timestamp)}</span>
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
                <div className="kv-row"><dt>Detected</dt><dd>{formatClock(open.timestamp)}</dd></div>
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
