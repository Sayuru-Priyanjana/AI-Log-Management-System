import { useEffect, useState } from 'react';
import {
  createUser, deleteUser, getRegisteredSystems, getUsers, updateUserSystems,
} from '../api';
import { useToast } from '../toast';

export default function UsersPage() {
  const toast = useToast();
  const [users, setUsers] = useState([]);
  const [systems, setSystems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const [form, setForm] = useState({ username: '', password: '', role: 'developer', systems: [] });
  const [editing, setEditing] = useState(null);   // { id, systems }

  const load = async () => {
    try {
      const [userData, systemData] = await Promise.all([getUsers(), getRegisteredSystems()]);
      setUsers(userData.users || []);
      setSystems(systemData.systems || []);
    } catch (err) {
      toast.error('Could not load users', { detail: err.message });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const toggle = (list, id) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

  const create = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      await createUser(form.username, form.password, form.role, form.systems);
      toast.success(`Created ${form.username}`);
      setForm({ username: '', password: '', role: 'developer', systems: [] });
      load();
    } catch (err) {
      toast.error('Could not create user', { detail: err.message });
    } finally {
      setBusy(false);
    }
  };

  const saveSystems = async () => {
    try {
      await updateUserSystems(editing.id, editing.systems);
      toast.success('Access updated');
      setEditing(null);
      load();
    } catch (err) {
      toast.error('Could not update access', { detail: err.message });
    }
  };

  const remove = async (user) => {
    if (!window.confirm(`Delete ${user.username}?`)) return;
    try {
      await deleteUser(user.id);
      toast.success(`Deleted ${user.username}`);
      load();
    } catch (err) {
      toast.error('Could not delete user', { detail: err.message });
    }
  };

  return (
    <div className="page">
      <div className="pagehead">
        <h2>Users</h2>
        <p>Developers see only the systems assigned to them; administrators see all of them.</p>
        <span className="spacer" />
        <span className="chip">{users.length} accounts</span>
      </div>

      <div className="card">
        <header><h3>Create account</h3></header>
        <form className="card-body col" onSubmit={create}>
          <div className="row" style={{ alignItems: 'flex-end' }}>
            <div className="field" style={{ flex: 1, minWidth: 150 }}>
              <label htmlFor="u-name">Username</label>
              <input id="u-name" className="input" value={form.username} required
                onChange={(e) => setForm({ ...form, username: e.target.value })} />
            </div>
            <div className="field" style={{ flex: 1, minWidth: 150 }}>
              <label htmlFor="u-pass">Password</label>
              <input id="u-pass" className="input" type="password" value={form.password} required
                autoComplete="new-password"
                onChange={(e) => setForm({ ...form, password: e.target.value })} />
            </div>
            <div className="field" style={{ width: 140 }}>
              <label htmlFor="u-role">Role</label>
              <select id="u-role" className="input" value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}>
                <option value="developer">Developer</option>
                <option value="admin">Administrator</option>
              </select>
            </div>
            <button type="submit" className="btn btn--primary" disabled={busy}>
              {busy ? 'Creating…' : 'Create'}
            </button>
          </div>

          {form.role === 'developer' && (
            <div className="field">
              <label>
                Systems
                {form.systems.length > 0 && <span className="dim">{form.systems.length} selected</span>}
              </label>
              {systems.length === 0
                ? <span className="hint">No clusters registered yet.</span>
                : (
                  <div className="chiprow">
                    {systems.map((system) => {
                      const on = form.systems.includes(system.id);
                      return (
                        <button key={system.id} type="button"
                          className={`chip ${on ? 'chip--accent' : ''}`}
                          style={{ cursor: 'pointer', height: 22, padding: '0 8px' }}
                          onClick={() => setForm({ ...form, systems: toggle(form.systems, system.id) })}>
                          {on ? '✓ ' : ''}{system.name}
                        </button>
                      );
                    })}
                  </div>
                )}
            </div>
          )}
        </form>
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1 }}>
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 50 }}>ID</th>
                <th style={{ width: '20%' }}>Username</th>
                <th style={{ width: 110 }}>Role</th>
                <th>Systems</th>
                <th style={{ width: 130 }}></th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan="5" className="empty">Loading…</td></tr>}
              {!loading && users.length === 0 && (
                <tr><td colSpan="5" className="empty">No accounts yet.</td></tr>
              )}
              {users.map((user) => {
                const isEditing = editing?.id === user.id;
                return (
                  <tr key={user.id}>
                    <td className="mono dim">{user.id}</td>
                    <td style={{ fontWeight: 500 }}>{user.username}</td>
                    <td>
                      <span className={`chip ${user.role === 'admin' ? 'chip--alt' : ''}`}>{user.role}</span>
                    </td>
                    <td>
                      {user.role === 'admin' ? (
                        <span className="dim">all systems</span>
                      ) : isEditing ? (
                        <div className="chiprow">
                          {systems.map((system) => {
                            const on = editing.systems.includes(system.id);
                            return (
                              <button key={system.id} type="button"
                                className={`chip ${on ? 'chip--accent' : ''}`}
                                style={{ cursor: 'pointer', height: 20 }}
                                onClick={() => setEditing({
                                  ...editing, systems: toggle(editing.systems, system.id),
                                })}>
                                {on ? '✓ ' : ''}{system.name}
                              </button>
                            );
                          })}
                        </div>
                      ) : user.systems?.length ? (
                        <div className="chiprow">
                          {user.systems.map((id) => <span key={id} className="chip chip--mono">{id}</span>)}
                        </div>
                      ) : (
                        <span className="dim">none</span>
                      )}
                    </td>
                    <td className="actions">
                      {user.role === 'developer' && (isEditing ? (
                        <>
                          <button type="button" className="btn btn--sm btn--primary"
                            onClick={saveSystems}>Save</button>
                          <button type="button" className="btn btn--sm btn--ghost"
                            onClick={() => setEditing(null)}>Cancel</button>
                        </>
                      ) : (
                        <button type="button" className="btn btn--sm btn--ghost"
                          onClick={() => setEditing({ id: user.id, systems: user.systems || [] })}>
                          Edit
                        </button>
                      ))}
                      {!isEditing && (
                        <button type="button" className="btn btn--sm btn--ghost btn--danger"
                          onClick={() => remove(user)}>Delete</button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
