import { useState } from 'react';
import { NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import AgentPage from './components/AgentPage';
import ChangePasswordModal from './components/ChangePasswordModal';
import ConfigurationPanel from './components/ConfigurationPanel';
import HealthBar from './components/HealthBar';
import IncidentsPanel from './components/IncidentsPanel';
import LoginPage from './components/LoginPage';
import SystemsPage from './components/SystemsPage';
import UsersPage from './components/UsersPage';
import WorkstationPage from './components/WorkstationPage';
import Scheduler from './components/Scheduler';
import DashboardPage from './components/DashboardPage';
import LogsPage from './components/LogsPage';
import FeedbackModal from './components/FeedbackModal';
import { usePreferences } from './preferences';
import './index.css';

/**
 * The shell: a 46px bar and everything else.
 *
 * The bar carries identity, navigation, live dependency status and the account
 * controls on one line — four things that each used to take a row of their own.
 * Below it is a single scrolling pane; pages decide their own internal layout,
 * and the two-pane ones run to the edges rather than sitting in a centred
 * column with empty gutters.
 *
 * `/incidents` stays unlinked. It breaks a running cluster on purpose, which is
 * worth typing a URL for.
 */
export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem('jwt'));
  const [role, setRole] = useState(() => localStorage.getItem('role'));
  const [username, setUsername] = useState(() => localStorage.getItem('username'));
  const [changingPassword, setChangingPassword] = useState(false);
  const [showFeedback, setShowFeedback] = useState(false);

  const handleLogin = (nextRole, nextUsername) => {
    setToken(localStorage.getItem('jwt'));
    setRole(nextRole);
    setUsername(nextUsername);
  };

  const handleLogout = () => {
    ['jwt', 'role', 'username'].forEach((key) => localStorage.removeItem(key));
    setToken(null);
    setRole(null);
    setUsername(null);
  };

  if (!token) return <LoginPage onLogin={handleLogin} />;

  const isAdmin = role === 'admin';

  return (
    <div className="app">
      <header className="topbar">
        <span className="topbar-brand">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 19h16M7 16V9M12 16V5M17 16v-4" />
          </svg>
          LogIntel
        </span>

        <span className="topbar-sep" />

        <nav className="nav">
          <NavLink to="/" end className={navClass}>Workstation</NavLink>
          <a href="/osd/" target="_blank" rel="noopener noreferrer">OpenSearch</a>
          {isAdmin && <NavLink to="/systems" className={navClass}>Systems</NavLink>}
          {isAdmin && <NavLink to="/users" className={navClass}>Users</NavLink>}
          {isAdmin && <NavLink to="/configuration" className={navClass}>Configuration</NavLink>}
        </nav>

        <span className="spacer" />

        <HealthBar />
        <span className="topbar-sep" />
        <span className="whoami"><strong>{username}</strong> · {role}</span>

        <button type="button" className="btn" title="Send Feedback" onClick={() => setShowFeedback(true)} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
          </svg>
          Feedback
        </button>

        <button type="button" className="iconbtn" title="Change password"
          onClick={() => setChangingPassword(true)}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="11" width="18" height="11" rx="2" />
            <path d="M7 11V7a5 5 0 0 1 10 0v4" />
          </svg>
        </button>
        <ThemeToggle />
        <button type="button" className="iconbtn" title="Sign out" onClick={handleLogout}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
            <polyline points="16 17 21 12 16 7" />
            <line x1="21" y1="12" x2="9" y2="12" />
          </svg>
        </button>
      </header>

      <div className="pane">
        <Scheduler />
        <Routes>
          <Route path="/" element={<WorkstationPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/logs" element={<LogsPage />} />
          <Route path="/agent" element={<AgentPage />} />
          {isAdmin && <Route path="/systems" element={<SystemsPage />} />}
          {isAdmin && <Route path="/users" element={<UsersPage />} />}
          {isAdmin && <Route path="/configuration" element={<ConfigurationPanel />} />}
          {isAdmin && <Route path="/incidents" element={<IncidentsPage />} />}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>

      {changingPassword && <ChangePasswordModal onClose={() => setChangingPassword(false)} />}
      {showFeedback && <FeedbackModal onClose={() => setShowFeedback(false)} />}
    </div>
  );
}

const navClass = ({ isActive }) => (isActive ? 'is-active' : undefined);

function ThemeToggle() {
  const { theme, toggleTheme } = usePreferences();
  const dark = theme === 'dark';
  return (
    <button type="button" className="iconbtn" onClick={toggleTheme}
      // Announces the destination, not the current state: a control labelled
      // "dark" while already dark reads as a status, and gets clicked twice.
      title={`Switch to ${dark ? 'light' : 'dark'} theme`}
      aria-label={`Switch to ${dark ? 'light' : 'dark'} theme`}>
      {dark ? (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z" />
        </svg>
      )}
    </button>
  );
}

function IncidentsPage() {
  const navigate = useNavigate();
  return <IncidentsPanel onInvestigate={(prefill) => navigate('/', { state: { prefill } })} />;
}
