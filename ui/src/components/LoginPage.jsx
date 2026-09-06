import { useState } from 'react';
import { login } from '../api';
import { useToast } from '../toast';

export default function LoginPage({ onLogin }) {
  const toast = useToast();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      const { token, role } = await login(username, password);
      localStorage.setItem('jwt', token);
      localStorage.setItem('role', role);
      localStorage.setItem('username', username);
      onLogin(role, username);
    } catch (err) {
      // A failed sign-in is the one message that must not move the form: the
      // password field is where the user's attention already is.
      toast.error('Sign in failed', { detail: err.message });
      setPassword('');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="signin">
      <div className="signin-card">
        <div className="signin-brand">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 19h16M7 16V9M12 16V5M17 16v-4" />
          </svg>
          LogIntel
        </div>

        <div className="card">
          <form className="card-body col" onSubmit={submit}>
            <div className="field">
              <label htmlFor="signin-user">Username</label>
              <input id="signin-user" className="input" value={username} autoFocus
                autoComplete="username" onChange={(e) => setUsername(e.target.value)} required />
            </div>
            <div className="field">
              <label htmlFor="signin-pass">Password</label>
              <input id="signin-pass" className="input" type="password" value={password}
                autoComplete="current-password"
                onChange={(e) => setPassword(e.target.value)} required />
            </div>
            <button type="submit" className="btn btn--primary" disabled={busy}
              style={{ width: '100%', marginTop: 2 }}>
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
