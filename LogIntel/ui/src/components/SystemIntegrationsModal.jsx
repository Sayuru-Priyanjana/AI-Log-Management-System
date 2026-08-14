import { useEffect, useState } from 'react';
import { getSystemIntegrations, testSystemIntegrations, updateSystemIntegrations } from '../api';
import { useToast } from '../toast';

const COMPONENT_LABEL = {
  opensearch: 'OpenSearch', prometheus: 'Prometheus', model: 'Agent model',
  incident_controller: 'Fluent Bit source',
};

/**
 * This system's configuration — where its detections get reported, whether to
 * scan for more, and the same connectivity status shown on the dashboard.
 *
 * Reached from the Workstation "Configuration" button and from "Edit" on the
 * system card. Deliberately not the nav bar's Configuration page: that one
 * edits the agent process's own connections (which OpenSearch, which model)
 * and is the same for every system; a Teams channel and a scan cadence belong
 * to *this* cluster, and editing them from a shared global page would make it
 * too easy to change the wrong system's settings.
 */
export default function SystemIntegrationsModal({ system, health, tests, onTest, onClose }) {
  const toast = useToast();
  const [values, setValues] = useState(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    getSystemIntegrations(system.id)
      .then((res) => setValues(res.values))
      .catch((err) => toast.error('Could not load integration settings', { detail: err.message }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [system.id]);

  const set = (name, value) => setValues((v) => ({ ...v, [name]: value }));

  const save = async () => {
    setSaving(true);
    try {
      const res = await updateSystemIntegrations(system.id, values);
      setValues(res.values);
      if (res.warning) toast.error('Saved, but not written down', { detail: res.warning });
      else toast.success(`Saved for ${system.name}`);
    } catch (err) {
      toast.error('Could not save', { detail: err.message });
    } finally {
      setSaving(false);
    }
  };

  const ping = async () => {
    setTesting(true);
    try {
      const res = await testSystemIntegrations(system.id);
      if (res.ok) toast.success('Message sent to Teams', { detail: res.detail });
      else toast.error('Could not send to Teams', { detail: res.detail });
    } catch (err) {
      toast.error('Ping failed', { detail: err.message });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal modal--wide" role="dialog" aria-modal="true">
        <header>
          <h3>Configuration — {system.name}</h3>
          <button type="button" className="iconbtn" onClick={onClose} aria-label="Close">×</button>
        </header>

        {health && (
          <div className="modal-body" style={{ paddingBottom: 0 }}>
            <span className="hint">External services</span>
            <div className="wsx-status">
              {Object.entries(health.components).filter(([key]) => key !== 'registry').map(([key, component]) => {
                const target = key === 'incident_controller' ? 'incidents' : key;
                return (
                  <span key={key} className="status-chip" title={component.url || component.error || ''}>
                    <span className={`dot dot--${tone(component.status)}`} />
                    {COMPONENT_LABEL[key] || key}
                    <button type="button" onClick={() => onTest(target)}
                      disabled={tests?.[target] === 'pending'}>
                      {tests?.[target] === 'pending' ? '…' : 'test'}
                    </button>
                  </span>
                );
              })}
            </div>
          </div>
        )}

        {!values ? (
          <div className="modal-body"><div className="empty">Loading…</div></div>
        ) : (
          <div className="modal-body">
            <span className="hint">Integrations — this system only</span>
            <div className="cfg-fields">
              <div className="field">
                <label>Channel name</label>
                <input className="input" value={values.teams_channel_name}
                  onChange={(e) => set('teams_channel_name', e.target.value)} />
                <span className="hint">A label only — Teams identifies the channel by the webhook URL.</span>
              </div>
              <div className="field">
                <label>Webhook URL</label>
                <input className="input" value={values.teams_webhook_url}
                  placeholder="https://…webhook.office.com/…"
                  onChange={(e) => set('teams_webhook_url', e.target.value)} />
                <span className="hint">An incoming webhook connector URL from this Teams channel.</span>
              </div>
            </div>

            <div className="row" style={{ marginTop: 4 }}>
              <button type="button" className="btn btn--sm" disabled={testing} onClick={ping}>
                {testing ? 'Sending…' : 'Ping'}
              </button>
            </div>

            <span className="hint" style={{ marginTop: 10, display: 'block' }}>Automation — this system only</span>
            <div className="field" style={{ marginTop: 4 }}>
              <label className="row" style={{ gap: 6, fontSize: 12.5 }}>
                <input type="checkbox" checked={values.auto_scan_enabled}
                  onChange={(e) => set('auto_scan_enabled', e.target.checked)} />
                Run a scheduled AI agent scan every day
                <input type="time" className="input input--sm" style={{ width: 100, marginLeft: 6 }}
                  value={values.scan_time} disabled={!values.auto_scan_enabled}
                  onChange={(e) => set('scan_time', e.target.value)} />
              </label>
              <span className="hint">
                Investigates this system with the agent once a day at the time above, the same as
                clicking Ask AI yourself. Uncheck to turn the daily run off.
              </span>
            </div>

            <div className="field" style={{ marginTop: 8 }}>
              <label className="row" style={{ gap: 6, fontSize: 12.5 }}>
                <input type="checkbox" checked={values.auto_investigate_alerts_enabled}
                  onChange={(e) => set('auto_investigate_alerts_enabled', e.target.checked)} />
                Auto investigate incoming alert with agent
              </label>
              <span className="hint">
                When a new alert or detection occurs, start an investigation automatically. You will see it running in the AI Agent window.
              </span>
            </div>

            <div className="cfg-fields" style={{ marginTop: 8 }}>
              <div className="field">
                <label className="row" style={{ gap: 6, height: 28, fontSize: 12.5 }}>
                  <input type="checkbox" checked={values.notify_on_alert_enabled}
                    onChange={(e) => set('notify_on_alert_enabled', e.target.checked)} />
                  Notify integrations for alerts
                </label>
                <span className="hint">Posts to Teams when an alert or detection is recorded for this system.</span>
              </div>
              <div className="field">
                <label className="row" style={{ gap: 6, height: 28, fontSize: 12.5 }}>
                  <input type="checkbox" checked={values.notify_on_scan_result_enabled}
                    onChange={(e) => set('notify_on_scan_result_enabled', e.target.checked)} />
                  Notify integrations with agent response
                </label>
                <span className="hint">Posts the auto-scanned agent results to Teams once it finishes.</span>
              </div>
            </div>
            <p className="hint" style={{ marginTop: 8 }}>
              These are saved and ready — there is no scheduler running yet to act on them, so the
              daily scan above does not fire on its own until one exists.
            </p>
          </div>
        )}

        <div className="modal-foot">
          <button type="button" className="btn" onClick={onClose}>Close</button>
          <button type="button" className="btn btn--primary" disabled={!values || saving} onClick={save}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

function tone(status) {
  if (status === 'ok') return 'ok';
  if (status === 'unreachable') return 'err';
  return 'warn';
}
