import { useEffect, useMemo, useState } from 'react';
import {
  getClusters, getHealth, getSettings, refreshSystems, testConnection, updateSettings,
} from '../api';
import { usePreferences } from '../preferences';
import { ErrorBanner } from './common';

const COMPONENT_LABEL = {
  opensearch: 'Log storage',
  prometheus: 'Metrics',
  model: 'Model',
  incident_controller: 'Testbed injector',
  registry: 'Cluster registry',
};

// Which live component a settings group is responsible for, so each group can
// offer a test that exercises what it just changed.
const GROUP_TARGET = {
  opensearch: 'opensearch',
  prometheus: 'prometheus',
  model: 'model',
};

const ZONE_SUGGESTIONS = [
  '+05:30', '+00:00', '+01:00', '+05:45', '+08:00', '-05:00', '-08:00',
  'Asia/Colombo', 'Asia/Kolkata', 'Europe/London', 'America/New_York', 'UTC',
];

/**
 * The connection surface, in one place.
 *
 * Everything here answers *where*, never *how*: which OpenSearch, which model,
 * which zone. The thresholds and window arithmetic are deliberately absent —
 * they are what the tests pin down, and a text box that quietly changed what
 * counts as an incident would make every stored investigation incomparable with
 * the next.
 */
export default function ConfigurationPanel() {
  const { zone, setZone } = usePreferences();
  const [fields, setFields] = useState(null);
  const [groups, setGroups] = useState([]);
  const [health, setHealth] = useState(null);
  const [clusters, setClusters] = useState(null);
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState(null);
  const [error, setError] = useState(null);
  const [tests, setTests] = useState({});

  const load = async () => {
    try {
      const [settings, healthData] = await Promise.all([getSettings(), getHealth()]);
      setFields(settings.fields);
      setGroups(settings.groups);
      setHealth(healthData);
      setError(null);
      if (settings.timezone?.value) setZone(settings.timezone.value);
    } catch (err) {
      setError(err.message);
    }
    try {
      setClusters(await getClusters());
    } catch {
      setClusters(null);   // the registry is empty or unreachable; not fatal here
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const byGroup = useMemo(() => {
    const out = {};
    for (const field of fields || []) {
      (out[field.group] = out[field.group] || []).push(field);
    }
    return out;
  }, [fields]);

  const dirty = Object.keys(draft).length > 0;

  const edit = (name, value) => setDraft((d) => ({ ...d, [name]: value }));

  const save = async () => {
    setSaving(true);
    setNotice(null);
    setError(null);
    try {
      const result = await updateSettings(draft);
      setFields(result.fields);
      setDraft({});
      if (result.timezone?.value) setZone(result.timezone.value);
      // The warning matters more than the success: a change that took effect but
      // was not written down will silently revert on the next restart.
      setNotice(result.warning
        || (result.changed.length
          ? `Applied ${result.changed.length} change(s). Clients were rebuilt.`
          : 'Nothing changed.'));
      setHealth(await getHealth());
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (target) => {
    setTests((t) => ({ ...t, [target]: { pending: true } }));
    try {
      const result = await testConnection(target);
      setTests((t) => ({ ...t, [target]: { pending: false, ...result } }));
    } catch (err) {
      setTests((t) => ({ ...t, [target]: { pending: false, ok: false, detail: err.message } }));
    }
  };

  if (error && !fields) return <ErrorBanner><strong>Cannot load settings.</strong> {error}</ErrorBanner>;
  if (!fields) return <p className="li-muted">Loading configuration…</p>;

  return (
    <div className="li-config">
      <div className="li-page-toolbar">
        <h2>Configuration</h2>
        <button type="button" className="li-btn" onClick={load}>Refresh</button>
      </div>

      <ConnectionStatus health={health} onTest={runTest} tests={tests} />

      {error && <ErrorBanner>{error}</ErrorBanner>}
      {notice && (
        <div className="glass-panel" style={{ padding: '12px 16px' }}>
          <span className="li-muted">{notice}</span>
        </div>
      )}

      {groups.map((group) => (
        <section key={group.id} className="glass-panel li-config-group">
          <div className="li-config-group-head">
            <h3>{group.label}</h3>
            <p className="li-muted">{group.description}</p>
          </div>

          <div className="li-config-fields">
            {(byGroup[group.id] || []).map((field) => (
              <SettingField
                key={field.name}
                field={field}
                draft={draft}
                zone={zone}
                onChange={edit}
              />
            ))}
          </div>

          {GROUP_TARGET[group.id] && (
            <div style={{ marginTop: '1rem', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <button type="button" className="li-btn"
                disabled={tests[GROUP_TARGET[group.id]]?.pending}
                onClick={() => runTest(GROUP_TARGET[group.id])}>
                {tests[GROUP_TARGET[group.id]]?.pending ? 'Testing…' : 'Test connection'}
              </button>
              <TestResult result={tests[GROUP_TARGET[group.id]]} />
            </div>
          )}
        </section>
      ))}

      <Clusters data={clusters} onRefresh={async () => {
        await refreshSystems();
        setClusters(await getClusters());
      }} />

      <div className="li-config-actions">
        <button type="button" className="li-btn li-btn--primary" disabled={!dirty || saving} onClick={save}>
          {saving ? 'Applying…' : `Apply ${dirty ? `(${Object.keys(draft).length})` : ''}`}
        </button>
        <button type="button" className="li-btn" disabled={!dirty || saving} onClick={() => setDraft({})}>
          Discard
        </button>
        <span className="li-muted">
          {dirty
            ? 'Changes take effect immediately and are saved to OpenSearch.'
            : 'No unsaved changes.'}
        </span>
      </div>
    </div>
  );
}

function SettingField({ field, draft, zone, onChange }) {
  const pending = Object.prototype.hasOwnProperty.call(draft, field.name);
  const value = pending ? draft[field.name] : (field.value ?? '');

  const control = () => {
    if (field.kind === 'boolean') {
      return (
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: '0.9rem' }}>
          <input type="checkbox" checked={Boolean(value)}
            onChange={(e) => onChange(field.name, e.target.checked)} />
          {value ? 'Enabled' : 'Disabled'}
        </label>
      );
    }
    if (field.kind === 'select') {
      return (
        <select className="glass-input" value={value}
          onChange={(e) => onChange(field.name, e.target.value)}>
          {field.options.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      );
    }
    if (field.kind === 'password') {
      return (
        <input className="glass-input" type="password" autoComplete="new-password"
          // A secret is never sent back by the API, so the box starts empty and
          // says whether one is already stored rather than showing dots that
          // stand for nothing.
          placeholder={field.is_set ? 'stored — type to replace' : 'not set'}
          value={pending ? String(draft[field.name] ?? '') : ''}
          onChange={(e) => onChange(field.name, e.target.value)} />
      );
    }
    if (field.name === 'display_timezone') {
      return (
        <>
          <input className="glass-input" list="tz-options" value={value}
            onChange={(e) => onChange(field.name, e.target.value)} />
          <datalist id="tz-options">
            {ZONE_SUGGESTIONS.map((option) => <option key={option} value={option} />)}
          </datalist>
          <span className="li-config-help">
            Now showing as <strong>{sample(pending ? value : zone)}</strong>
          </span>
        </>
      );
    }
    return (
      <input className="glass-input" type={field.kind === 'number' ? 'number' : 'text'}
        value={value} onChange={(e) => onChange(field.name, e.target.value)} />
    );
  };

  return (
    <div className="li-config-field">
      <div className="li-config-field-head">
        <label className="li-label">{field.label}</label>
        <span className={`li-source li-source--${field.source}`}>{field.source}</span>
        {pending && <span className="li-source li-source--saved">edited</span>}
      </div>
      {control()}
      {field.help && <span className="li-config-help">{field.help}</span>}
    </div>
  );
}

/** What the clock reads right now in a candidate zone, so the field is checkable. */
function sample(zone) {
  const now = new Date();
  try {
    if (/^[+-]\d{1,2}:?\d{2}$/.test(zone)) {
      const m = /^([+-])(\d{1,2}):?(\d{2})$/.exec(zone);
      const minutes = (Number(m[2]) * 60 + Number(m[3])) * (m[1] === '-' ? -1 : 1);
      const shifted = new Date(now.getTime() + minutes * 60000);
      return `${String(shifted.getUTCHours()).padStart(2, '0')}:${String(shifted.getUTCMinutes()).padStart(2, '0')}`;
    }
    return new Intl.DateTimeFormat('en-GB', {
      timeZone: zone, hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(now);
  } catch {
    return 'not a valid zone';
  }
}

function ConnectionStatus({ health, onTest, tests }) {
  if (!health) return null;
  const entries = Object.entries(health.components || {});
  return (
    <section>
      <div className="li-section-head" style={{ marginBottom: 10 }}>
        <h4 style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.9px', color: 'var(--text-tertiary)' }}>
          Network status
        </h4>
        <span className="li-muted">overall: {health.status}</span>
      </div>
      <div className="li-config-status">
        {entries.map(([key, component]) => (
          <div key={key} className={`glass-panel li-status-card li-status-card--${component.status}`}>
            <div className="li-status-card-head">
              <span className="li-healthbar-dot" style={{ color: toneOf(component.status) }} />
              <span className="li-status-card-name">{COMPONENT_LABEL[key] || key}</span>
              <span className="li-spacer" />
              <span className="li-muted" style={{ fontSize: '0.78rem' }}>{component.status}</span>
            </div>
            <div className="li-status-card-detail">
              {component.provider ? `${component.provider} · ` : ''}
              {component.model ? `${component.model} · ` : ''}
              {component.url || component.error || '—'}
            </div>
            {component.hint && <div className="li-status-card-hint">{component.hint}</div>}
            {(component.problems || []).map((problem) => (
              <div key={problem} className="li-status-card-hint">{problem}</div>
            ))}
            {['opensearch', 'prometheus', 'model', 'incident_controller'].includes(key) && (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 4, flexWrap: 'wrap' }}>
                <button type="button" className="li-raw-button"
                  onClick={() => onTest(key === 'incident_controller' ? 'incidents' : key)}>
                  Test
                </button>
                <TestResult result={tests[key === 'incident_controller' ? 'incidents' : key]} />
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function TestResult({ result }) {
  if (!result || result.pending) return null;
  return (
    <span className={`li-test-result li-test-result--${result.ok ? 'ok' : 'bad'}`}>
      {result.ok ? '✓ ' : '✕ '}{result.detail}
    </span>
  );
}

function toneOf(status) {
  if (status === 'ok') return 'var(--success)';
  if (status === 'unreachable') return 'var(--error)';
  return 'var(--warning)';
}

/**
 * The clusters shipping data in.
 *
 * Nothing is "connected" from this end: a cluster joins by writing documents and
 * appears here once it has. So this reports what has arrived, and states what a
 * new one has to send — rather than offering a registration button that would
 * only ever be a lie about where the coupling is.
 */
function Clusters({ data, onRefresh }) {
  const [busy, setBusy] = useState(false);
  if (!data) return null;

  const refresh = async () => {
    setBusy(true);
    try { await onRefresh(); } finally { setBusy(false); }
  };

  return (
    <section className="glass-panel li-config-group">
      <div className="li-config-group-head" style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
        <div>
          <h3>Connected clusters</h3>
          <p className="li-muted">
            Discovered from the data itself. A cluster appears here once its logs
            reach <code className="li-mono">{data.ingest.log_index}</code> — there is
            no registration step to forget.
          </p>
        </div>
        <button type="button" className="li-btn" disabled={busy} onClick={refresh}>
          {busy ? 'Rescanning…' : 'Rescan'}
        </button>
      </div>

      {data.clusters.length === 0 ? (
        <p className="li-empty">
          No cluster has shipped logs yet. Point a shipper at the endpoint below and
          it will appear here within a few minutes.
        </p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: '1.25rem' }}>
          {data.clusters.map((cluster) => (
            <div key={cluster.id} className="glass-panel li-cluster">
              <div className="li-cluster-head">
                <strong>{cluster.name}</strong>
                <span className="li-chip li-chip--service">{cluster.id}</span>
                <span className="li-spacer" />
                <span className="li-muted">
                  {cluster.services.length} service{cluster.services.length === 1 ? '' : 's'}
                </span>
              </div>
              <div className="li-chiprow">
                {cluster.environments.map((env) => <span key={env} className="li-chip">{env}</span>)}
                {cluster.services.map((service) => (
                  <span key={service.name} className="li-chip" title={`${service.log_count} documents`}>
                    {service.name}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <h4 style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.9px', color: 'var(--text-tertiary)', marginBottom: 8 }}>
        Connecting another cluster
      </h4>
      <p className="li-muted" style={{ marginBottom: 10 }}>
        Ship to <code className="li-mono">{data.ingest.opensearch_url}</code>, index{' '}
        <code className="li-mono">{data.ingest.log_index}</code>. These fields are what
        the pipeline filters on — logs without them are visible in Discover and
        invisible to the agent:
      </p>
      <div className="li-table-wrap">
        <table className="li-ingest-table">
          <thead><tr><th>Field</th><th>Requirement</th></tr></thead>
          <tbody>
            {data.ingest.required_fields.map((field) => (
              <tr key={field.field}><td>{field.field}</td><td>{field.note}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
