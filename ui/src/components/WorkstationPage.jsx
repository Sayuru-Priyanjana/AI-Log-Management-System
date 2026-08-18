import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getHealth, getRecentInvestigations, getSystemIntegrations, getSystems, testConnection } from '../api';
import { useToast } from '../toast';
import ActivitiesPanel from './ActivitiesPanel';
import AlertsPanel from './AlertsPanel';
import SystemHealthPanel from './SystemHealthPanel';
import SystemIntegrationsModal from './SystemIntegrationsModal';

import { useInvestigation } from '../InvestigationContext';

const COMPONENT_LABEL = {
  opensearch: 'OpenSearch', prometheus: 'Prometheus', model: 'Agent model',
  registry: 'Registry',
};

/**
 * The dashboard: pick a system, see its shape, act on it.
 *
 * Three columns once a system is selected — details and services on the left,
 * what has happened in the middle, what needs attention on the right — plus a
 * status row above them and the two things you actually do from here: open the
 * agent, or open configuration. Both are their own screens; this one only
 * orients.
 */
export default function WorkstationPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const { status: agentStatus, meta: agentMeta } = useInvestigation();

  const [systems, setSystems] = useState([]);
  const [health, setHealth] = useState(null);
  const [investigations, setInvestigations] = useState([]);
  const [integrations, setIntegrations] = useState(null);
  const [editingIntegrations, setEditingIntegrations] = useState(false);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState(() => localStorage.getItem('lastSystemId') || null);
  const [tests, setTests] = useState({});

  useEffect(() => {
    let mounted = true;
    Promise.all([getSystems(), getHealth()])
      .then(([systemData, healthData]) => {
        if (!mounted) return;
        const list = systemData.systems || [];
        setSystems(list);
        setHealth(healthData);
        setSelectedId((current) => {
          const next = list.some((s) => s.id === current) ? current : (list[0]?.id || null);
          if (next) localStorage.setItem('lastSystemId', next);
          return next;
        });
      })
      .catch((err) => mounted && toast.error('Could not load the workstation', { detail: err.message }))
      .finally(() => mounted && setLoading(false));
    return () => { mounted = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    getRecentInvestigations(50, selectedId)
      .then((res) => setInvestigations(res.investigations || []))
      .catch(() => setInvestigations([]));
    // This system's own settings, not the agent's — see SystemIntegrationsModal.
    getSystemIntegrations(selectedId)
      .then((res) => setIntegrations(res.values))
      .catch(() => setIntegrations(null));
  }, [selectedId]);

  const selectSystem = (id) => {
    setSelectedId(id);
    localStorage.setItem('lastSystemId', id);
  };

  const runTest = async (target) => {
    setTests((t) => ({ ...t, [target]: 'pending' }));
    try {
      const result = await testConnection(target);
      setTests((t) => ({ ...t, [target]: result.ok ? 'ok' : 'bad' }));
      if (result.ok) toast.success(`${COMPONENT_LABEL[target] || target} reachable`, { detail: result.detail });
      else toast.error(`${COMPONENT_LABEL[target] || target} not reachable`, { detail: result.detail });
    } catch (err) {
      setTests((t) => ({ ...t, [target]: 'bad' }));
      toast.error('Test failed', { detail: err.message });
    }
  };

  const selected = systems.find((s) => s.id === selectedId);
  const teamsConfigured = Boolean(integrations?.teams_webhook_url);

  return (
    <div className="wsx">
      <div className="wsx-top">
        <select className="input" value={selectedId || ''} disabled={loading || systems.length === 0}
          onChange={(e) => selectSystem(e.target.value)}>
          {systems.length === 0 && <option value="">No systems discovered yet</option>}
          {systems.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <span className="spacer" />
        
        {(agentStatus === 'connecting' || agentStatus === 'streaming') && (
          <span className="row" style={{ gap: 6, color: 'var(--accent)', marginRight: 16, cursor: 'pointer' }}
                onClick={() => navigate('/agent', { state: { system_id: selectedId } })}>
            <span className="dot dot--ok" style={{ animation: 'toast-in 1s infinite alternate' }} />
            AI Agent is running {agentMeta?.label ? `(${agentMeta.label})` : ''}
          </span>
        )}

        <button type="button" className="btn" disabled={!selected} onClick={() => navigate('/dashboard')}>
          Dashboard
        </button>
        <button type="button" className="btn btn--primary" disabled={!selected}
          onClick={() => navigate('/agent', { state: { system_id: selectedId } })}>
          AI Agent
        </button>
        {localStorage.getItem('role') === 'admin' && (
          // This button opens *this system's* configuration — its Teams channel,
          // its automation settings. The agent's own connections (which
          // OpenSearch, which model) are a different, global thing, reached
          // only from the nav bar's Configuration link.
          <button type="button" className="btn" disabled={!selected}
            onClick={() => setEditingIntegrations(true)}>
            Configuration
          </button>
        )}
      </div>

      {health && (
        <div className="wsx-status">
          {Object.entries(health.components).filter(([key]) => key !== 'registry').map(([key, component]) => {
            const target = key;
            return (
              <span key={key} className="status-chip" title={component.url || component.error || ''}>
                <span className={`dot dot--${tone(component.status)}`} />
                {COMPONENT_LABEL[key] || key}
                <button type="button" onClick={() => runTest(target)}
                  disabled={tests[target] === 'pending'}>
                  {tests[target] === 'pending' ? '…' : 'test'}
                </button>
              </span>
            );
          })}
        </div>
      )}

      {loading ? (
        <div className="empty">Loading…</div>
      ) : !selected ? (
        <div className="empty" style={{ marginTop: 40 }}>
          No systems have shipped logs yet. Once one does, it appears here automatically.
        </div>
      ) : (
        <div className="wsx-columns">
          <div className="wsx-col">
            <div className="card">
              <header><h3>System</h3></header>
              <div className="card-body">
                <dl className="kv-list">
                  <div className="kv-row"><dt>Name</dt><dd>{selected.name}</dd></div>
                  <div className="kv-row"><dt>ID</dt><dd className="mono">{selected.id}</dd></div>
                  <div className="kv-row"><dt>Environment</dt>
                    <dd><div className="chiprow">
                      {(selected.environments || []).map((e) => <span key={e} className="chip">{e}</span>)}
                    </div></dd>
                  </div>
                  <div className="kv-row"><dt>Agent (Fluent Bit)</dt>
                    <dd>
                      {selected.services?.some(s => s.name === 'logintel-agent' || s.name === 'fluent-bit') ? (
                        <><span className="dot dot--ok" /> Active</>
                      ) : (
                        <><span className="dot dot--degraded" /> Degraded</>
                      )}
                    </dd>
                  </div>
                  <div className="kv-row"><dt>Notify via</dt>
                    <dd className="row" style={{ gap: 6 }}>
                      Teams
                      {teamsConfigured
                        ? <span className="chip chip--ok">{integrations.teams_channel_name || 'configured'}</span>
                        : <span className="chip">not configured</span>}
                      <button type="button" className="btn btn--sm btn--ghost"
                        onClick={() => setEditingIntegrations(true)}>Edit</button>
                    </dd>
                  </div>
                </dl>
              </div>
            </div>

            {editingIntegrations && (
              <SystemIntegrationsModal system={selected} health={health} tests={tests} onTest={runTest}
                onClose={() => {
                  setEditingIntegrations(false);
                  getSystemIntegrations(selected.id).then((res) => setIntegrations(res.values)).catch(() => {});
                }} />
            )}

            <div className="card card--fill">
              <header>
                <h3>Services</h3>
                <span className="spacer" />
                <span className="dim">{selected.services?.length || 0} shipping logs</span>
              </header>
              <div className="card-body" style={{ padding: 0 }}>
                {selected.services?.length ? (
                  <table className="table">
                    <thead><tr><th>Service</th><th style={{ width: 100, textAlign: 'right' }}>Docs</th></tr></thead>
                    <tbody>
                      {[...selected.services].sort((a, b) => (b.log_count || 0) - (a.log_count || 0))
                        .map((service) => (
                          <tr key={service.name}>
                            <td className="mono">{service.name}</td>
                            <td className="num">{(service.log_count || 0).toLocaleString()}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                ) : <div className="empty">Nothing has shipped logs for this system yet.</div>}
              </div>
            </div>
          </div>

          <div className="wsx-col" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex' }}>
              <ActivitiesPanel systemId={selected.id} investigations={investigations} />
            </div>
            <div>
              <SystemHealthPanel systemId={selected.id} />
            </div>
          </div>

          <div className="wsx-col">
            <AlertsPanel system={selected} />
          </div>
        </div>
      )}
    </div>
  );
}

function tone(status) {
  if (status === 'ok') return 'ok';
  if (status === 'unreachable') return 'err';
  return 'warn';
}
