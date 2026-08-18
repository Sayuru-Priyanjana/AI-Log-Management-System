import { useEffect, useMemo, useState } from 'react';
import {
  getClusters, getHealth, getSettings, refreshSystems, testConnection, updateSettings,
} from '../api';
import { usePreferences } from '../preferences';
import { useToast } from '../toast';

const COMPONENT_LABEL = {
  opensearch: 'Log storage',
  prometheus: 'Metrics',
  model: 'Model',
  registry: 'Registry',
};

// Which live component a settings group is responsible for, so each group can
// test exactly what it just changed.
const GROUP_TARGET = { opensearch: 'opensearch', prometheus: 'prometheus', model: 'model' };

const ZONES = [
  '+05:30', '+00:00', '+01:00', '+05:45', '+08:00', '-05:00', '-08:00',
  'Asia/Colombo', 'Asia/Kolkata', 'Europe/London', 'America/New_York', 'UTC',
];

/**
 * The connection surface.
 *
 * Everything here answers *where*, never *how*: which OpenSearch, which model,
 * which zone. Thresholds and window arithmetic are deliberately absent — they
 * are what the tests pin down, and a text box that quietly changed what counts
 * as an incident would make every stored investigation incomparable with the
 * next.
 */
export default function ConfigurationPanel() {
  const toast = useToast();
  const { zone, setZone, setDefaultHours } = usePreferences();
  const [fields, setFields] = useState(null);
  const [groups, setGroups] = useState([]);
  const [health, setHealth] = useState(null);
  const [clusters, setClusters] = useState(null);
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);
  const [tests, setTests] = useState({});

  const load = async () => {
    try {
      const [settings, healthData] = await Promise.all([getSettings(), getHealth()]);
      setFields(settings.fields);
      setGroups(settings.groups);
      setHealth(healthData);
      if (settings.timezone?.value) setZone(settings.timezone.value);
    } catch (err) {
      toast.error('Could not load settings', { detail: err.message });
    }
    try {
      setClusters(await getClusters());
    } catch {
      setClusters(null);   // registry empty or unreachable; not fatal here
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const byGroup = useMemo(() => {
    const out = {};
    for (const field of fields || []) (out[field.group] = out[field.group] || []).push(field);
    return out;
  }, [fields]);

  const dirty = Object.keys(draft).length;

  const save = async () => {
    setSaving(true);
    try {
      const result = await updateSettings(draft);
      setFields(result.fields);
      setDraft({});
      if (result.timezone?.value) setZone(result.timezone.value);
      if (result.default_investigation_hours?.value) setDefaultHours(result.default_investigation_hours.value);
      // The warning matters more than the success: a change that took effect
      // but was not written down reverts silently on the next restart.
      if (result.warning) toast.error('Applied, but not saved', { detail: result.warning });
      else if (result.changed.length) {
        toast.success(`Applied ${result.changed.length} change${result.changed.length > 1 ? 's' : ''}`,
          { detail: result.changed.join(', ') });
      } else toast.info('Nothing changed');
      setHealth(await getHealth());
    } catch (err) {
      toast.error('Could not apply settings', { detail: err.message });
    } finally {
      setSaving(false);
    }
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

  if (!fields) return <div className="page"><div className="empty">Loading configuration…</div></div>;

  return (
    <div className="page">
      <div className="pagehead">
        <h2>Configuration</h2>
        <p>Where this agent connects. Takes effect immediately and is saved to OpenSearch.</p>
        <span className="spacer" />
        <button type="button" className="btn btn--sm" onClick={load}>Reload</button>
      </div>

      <Status health={health} tests={tests} onTest={runTest} />

      {groups.map((group) => (
        <div key={group.id} className="card">
          <header>
            <h3>{group.label}</h3>
            <p>{group.description}</p>
            <span className="spacer" />
            {GROUP_TARGET[group.id] && (
              <button type="button" className="btn btn--sm"
                disabled={tests[GROUP_TARGET[group.id]] === 'pending'}
                onClick={() => runTest(GROUP_TARGET[group.id])}>
                {tests[GROUP_TARGET[group.id]] === 'pending' ? 'Testing…' : 'Test'}
              </button>
            )}
          </header>
          <div className="card-body">
            <div className="cfg-fields">
              {(byGroup[group.id] || []).map((field) => (
                <Field key={field.name} field={field} draft={draft} zone={zone}
                  onChange={(name, value) => setDraft((d) => ({ ...d, [name]: value }))} />
              ))}
            </div>
          </div>
        </div>
      ))}

      <Clusters data={clusters} onRefresh={async () => {
        try {
          await refreshSystems();
          setClusters(await getClusters());
          toast.success('Registry rescanned');
        } catch (err) {
          toast.error('Rescan failed', { detail: err.message });
        }
      }} />

      <div className="cfg-actions">
        <button type="button" className="btn btn--primary" disabled={!dirty || saving} onClick={save}>
          {saving ? 'Applying…' : dirty ? `Apply ${dirty} change${dirty > 1 ? 's' : ''}` : 'Apply'}
        </button>
        <button type="button" className="btn" disabled={!dirty || saving} onClick={() => setDraft({})}>
          Discard
        </button>
        <span className="dim">{dirty ? 'Unsaved changes' : 'No unsaved changes'}</span>
      </div>
    </div>
  );
}

function Status({ health, tests, onTest }) {
  if (!health) return null;
  return (
    <div className="cfg-status">
      {Object.entries(health.components || {}).map(([key, component]) => {
        const target = key;
        return (
          <div key={key} className="cfg-node">
            <div className="cfg-node-head">
              <span className={`dot dot--${tone(component.status)}`} />
              <span className="cfg-node-name">{COMPONENT_LABEL[key] || key}</span>
              <span className="spacer" />
              <span className="dim">{component.status}</span>
              {key !== 'registry' && (
                <button type="button" className="btn btn--sm btn--ghost"
                  disabled={tests[target] === 'pending'} onClick={() => onTest(target)}>
                  {tests[target] === 'pending' ? '…' : 'Test'}
                </button>
              )}
            </div>
            <div className="cfg-node-detail">
              {[component.provider, component.model, component.url || component.error]
                .filter(Boolean).join(' · ') || '—'}
            </div>
            {component.hint && <div className="cfg-node-hint">{component.hint}</div>}
            {(component.problems || []).map((problem) => (
              <div key={problem} className="cfg-node-hint">{problem}</div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

function Field({ field, draft, zone, onChange }) {
  const pending = Object.prototype.hasOwnProperty.call(draft, field.name);
  const value = pending ? draft[field.name] : (field.value ?? '');

  return (
    <div className="field">
      <label>
        {field.label}
        <span className={`src src--${pending ? 'edited' : field.source}`}>
          {pending ? 'edited' : field.source}
        </span>
      </label>

      {field.kind === 'boolean' ? (
        <label className="row" style={{ gap: 6, height: 28, fontSize: 12.5 }}>
          <input type="checkbox" checked={Boolean(value)}
            onChange={(e) => onChange(field.name, e.target.checked)} />
          {value ? 'Enabled' : 'Disabled'}
        </label>
      ) : field.kind === 'select' ? (
        <select className="input" value={value} onChange={(e) => onChange(field.name, e.target.value)}>
          {field.options.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      ) : field.kind === 'password' ? (
        <input className="input" type="password" autoComplete="new-password"
          // A secret is never returned by the API, so the box starts empty and
          // says whether one is stored rather than showing dots meaning nothing.
          placeholder={field.is_set ? 'stored — type to replace' : 'not set'}
          value={pending ? String(draft[field.name] ?? '') : ''}
          onChange={(e) => onChange(field.name, e.target.value)} />
      ) : field.name === 'display_timezone' ? (
        <>
          <input className="input" list="zone-options" value={value}
            onChange={(e) => onChange(field.name, e.target.value)} />
          <datalist id="zone-options">
            {ZONES.map((option) => <option key={option} value={option} />)}
          </datalist>
          <span className="hint">now {sample(pending ? value : zone)}</span>
        </>
      ) : (
        <input className="input" type={field.kind === 'number' ? 'number' : 'text'}
          value={value} onChange={(e) => onChange(field.name, e.target.value)} />
      )}

      {field.help && field.name !== 'display_timezone' && <span className="hint">{field.help}</span>}
    </div>
  );
}

/** What the clock reads right now in a candidate zone, so the field is checkable. */
function sample(zone) {
  const now = new Date();
  try {
    const match = /^([+-])(\d{1,2}):?(\d{2})$/.exec(zone || '');
    if (match) {
      const minutes = (Number(match[2]) * 60 + Number(match[3])) * (match[1] === '-' ? -1 : 1);
      const shifted = new Date(now.getTime() + minutes * 60000);
      return `${pad(shifted.getUTCHours())}:${pad(shifted.getUTCMinutes())}`;
    }
    return new Intl.DateTimeFormat('en-GB', {
      timeZone: zone, hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(now);
  } catch {
    return 'not a valid zone';
  }
}
const pad = (n) => String(n).padStart(2, '0');

/**
 * Nothing is connected from this end: a cluster joins by writing documents and
 * appears once it has. So this reports what arrived and states the field
 * contract, rather than offering a button that would misrepresent the coupling.
 */
function Clusters({ data, onRefresh }) {
  if (!data) return null;
  return (
    <div className="card">
      <header>
        <h3>Connected clusters</h3>
        <p>Discovered from the data. A cluster appears once its logs reach <code>{data.ingest.log_index}</code>.</p>
        <span className="spacer" />
        <button type="button" className="btn btn--sm" onClick={onRefresh}>Rescan</button>
      </header>

      {data.clusters.length === 0 ? (
        <div className="empty">Nothing has shipped logs yet.</div>
      ) : (
        <table className="table">
          <thead>
            <tr><th style={{ width: '22%' }}>Cluster</th><th style={{ width: 140 }}>Environments</th><th>Services</th></tr>
          </thead>
          <tbody>
            {data.clusters.map((cluster) => (
              <tr key={cluster.id}>
                <td>
                  <div style={{ fontWeight: 500 }}>{cluster.name}</div>
                  <div className="mono dim">{cluster.id}</div>
                </td>
                <td><div className="chiprow">{cluster.environments.map((e) => <span key={e} className="chip">{e}</span>)}</div></td>
                <td>
                  <div className="chiprow">
                    {cluster.services.map((service) => (
                      <span key={service.name} className="chip chip--mono"
                        title={`${service.log_count} documents`}>{service.name}</span>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="card-body" style={{ borderTop: '1px solid var(--border)' }}>
        <h4 style={{ marginBottom: 6 }}>Fields a new cluster must ship</h4>
        <p className="hint" style={{ marginBottom: 8 }}>
          Send to <code>{data.ingest.opensearch_url}</code>, index <code>{data.ingest.log_index}</code>.
          Logs without these are visible in Discover and invisible to the agent.
        </p>
        <table className="table">
          <tbody>
            {data.ingest.required_fields.map((field) => (
              <tr key={field.field}>
                <td className="mono" style={{ width: 170, color: 'var(--accent)' }}>{field.field}</td>
                <td className="dim">{field.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function tone(status) {
  if (status === 'ok') return 'ok';
  if (status === 'unreachable') return 'err';
  return 'warn';
}
