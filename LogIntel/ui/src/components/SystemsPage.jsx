import { useEffect, useState } from 'react';
import { deleteSystem, getRegisteredSystems, registerSystem } from '../api';
import { useToast } from '../toast';

export default function SystemsPage() {
  const toast = useToast();
  const [systems, setSystems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  // The token is shown once, on registration. It is held here rather than in
  // the table because it is not a property of the system — it is a secret the
  // server will not repeat, and the row is the wrong place to imply otherwise.
  const [issued, setIssued] = useState(null);

  const load = async () => {
    try {
      const data = await getRegisteredSystems();
      setSystems(data.systems || []);
    } catch (err) {
      toast.error('Could not load systems', { detail: err.message });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const register = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      const data = await registerSystem(name);
      setIssued(data.system);
      setName('');
      toast.success(`Registered ${data.system.id}`, { detail: 'Copy the token now — it is not shown again.' });
      load();
    } catch (err) {
      toast.error('Could not register system', { detail: err.message });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (system) => {
    if (!window.confirm(`Delete ${system.name}? Its agent will stop being accepted.`)) return;
    try {
      await deleteSystem(system.id);
      toast.success(`Deleted ${system.name}`);
      load();
    } catch (err) {
      toast.error('Could not delete system', { detail: err.message });
    }
  };

  const helm = issued && [
    'helm install logintel-agent \\',
    '  oci://ghcr.io/sayuru-priyanjana/logintel-agent \\',
    '  --version 0.1.3 \\',
    '  -n logintel \\',
    '  --create-namespace \\',
    '  --set namespace=logintel \\',
    '  --set central.url="http://YOUR_CENTRAL_IP" \\',
    `  --set central.systemName="${issued.name}" \\`,
    `  --set auth.clusterId="${issued.id}" \\`,
    `  --set auth.token="${issued.token}"`,
  ].join('\n');

  return (
    <div className="page">
      <div className="pagehead">
        <h2>Systems</h2>
        <p>Each registered cluster gets an id and a token its agent authenticates with.</p>
        <span className="spacer" />
        <span className="chip">{systems.length} registered</span>
      </div>

      <div className="card">
        <header><h3>Register a cluster</h3></header>
        <form className="card-body row" onSubmit={register} style={{ alignItems: 'flex-end', maxWidth: 600, gap: 16 }}>
          <div className="field" style={{ width: 300 }}>
            <label htmlFor="sys-name">Name</label>
            <input id="sys-name" className="input" value={name} placeholder="prod-eu-west"
              onChange={(e) => setName(e.target.value)} required />
          </div>
          <button type="submit" className="btn btn--primary" disabled={busy || !name.trim()} style={{ minWidth: 100 }}>
            {busy ? 'Registering…' : 'Register'}
          </button>
        </form>

        {issued && (
          <div className="card-body" style={{ borderTop: '1px solid var(--border)' }}>
            <div className="row" style={{ marginBottom: 6 }}>
              <h4 style={{ color: 'var(--ok)' }}>Token for {issued.id}</h4>
              <span className="spacer" />
              <button type="button" className="btn btn--sm" onClick={() => {
                navigator.clipboard.writeText(helm)
                  .then(() => toast.success('Command copied'))
                  .catch(() => toast.error('Clipboard not available'));
              }}>Copy command</button>
              <button type="button" className="btn btn--sm btn--ghost" onClick={() => setIssued(null)}>
                Dismiss
              </button>
            </div>
            <pre className="block">{helm}</pre>
          </div>
        )}
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1 }}>
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr><th style={{ width: '30%' }}>ID</th><th>Name</th><th style={{ width: 90 }}></th></tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan="3" className="empty">Loading…</td></tr>}
              {!loading && systems.length === 0 && (
                <tr><td colSpan="3" className="empty">No clusters registered yet.</td></tr>
              )}
              {systems.map((system) => (
                <tr key={system.id}>
                  <td className="mono">{system.id}</td>
                  <td>{system.name}</td>
                  <td className="actions">
                    <button type="button" className="btn btn--sm btn--ghost btn--danger"
                      onClick={() => remove(system)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
