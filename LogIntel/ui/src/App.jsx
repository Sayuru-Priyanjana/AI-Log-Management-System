import { useState } from 'react';
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import ConfigurationPanel from './components/ConfigurationPanel';
import HealthBar from './components/HealthBar';
import IncidentsPanel from './components/IncidentsPanel';
import InvestigationForm from './components/InvestigationForm';
import InvestigationResults from './components/InvestigationResults';
import { usePreferences } from './preferences';
import './index.css';

/**
 * Three routes, one of them deliberately unlinked.
 *
 * `/` is the product and `/configuration` is where its connections live.
 * `/incidents` breaks a live cluster on purpose so answers can be checked
 * against a known cause — reachable by typing the URL, which is the right amount
 * of friction for an action that degrades a running system.
 */
export default function App() {
  return (
    <>
      <header className="li-topbar">
        <div className="li-topbar-inner">
          <div className="li-brand">
            <span className="li-brand-mark">
              <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 19h16M7 16V9M12 16V5M17 16v-4" />
              </svg>
            </span>
            <div className="li-header">
              <h1>Log analysis</h1>
              <p>Deterministic evidence extraction with a narrowly-scoped model on top.</p>
            </div>
          </div>

          <nav className="li-nav">
            <NavLink to="/" end className={navClass}>Investigate</NavLink>
            <a href="/osd/" target="_blank" rel="noopener noreferrer" className="li-nav-link">Dashboards</a>
            <NavLink to="/configuration" className={navClass}>Configuration</NavLink>
          </nav>

          <span className="li-spacer" />
          <HealthBar />
          <ThemeToggle />
        </div>
      </header>

      <div className="container">
        <Routes>
          <Route path="/" element={<InvestigatePage />} />
          <Route path="/configuration" element={<ConfigurationPage />} />
          <Route path="/incidents" element={<IncidentsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </>
  );
}

const navClass = ({ isActive }) => `li-nav-link ${isActive ? 'li-nav-link--active' : ''}`;

function ThemeToggle() {
  const { theme, toggleTheme } = usePreferences();
  const dark = theme === 'dark';
  return (
    <button
      type="button"
      className="li-icon-button"
      onClick={toggleTheme}
      // Announces the destination, not the current state: a button labelled
      // "dark" while already dark reads as a status, and gets clicked twice.
      title={`Switch to ${dark ? 'light' : 'dark'} theme`}
      aria-label={`Switch to ${dark ? 'light' : 'dark'} theme`}
    >
      {dark ? (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      ) : (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z" />
        </svg>
      )}
    </button>
  );
}

function InvestigatePage() {
  const location = useLocation();
  // Handed over by the incidents route. Read from navigation state rather than
  // lifted app state so a reload of `/` starts clean instead of re-filling a
  // form from an incident the user has since stopped.
  const [prefill] = useState(location.state?.prefill || null);
  const [request, setRequest] = useState(null);

  // A "next step" the agent should take on itself: re-run against the same
  // system and environment with the step as the new question. Keyed by
  // timestamp so asking the same follow-up twice actually re-runs it.
  const handleFollowUp = (question) => {
    if (!request) return;
    setRequest({ ...request, question, _at: Date.now() });
  };

  if (!request) {
    return (
      <main className="animate-fade-in" style={{ width: '100%', maxWidth: 640, margin: '0 auto' }}>
        <InvestigationForm onSubmit={setRequest} initial={prefill} />
      </main>
    );
  }

  return (
    <main className="animate-fade-in" style={{ width: '100%' }}>
      <div className="li-page-toolbar">
        <h2>Investigation</h2>
        <button type="button" className="li-btn" onClick={() => setRequest(null)}>
          New investigation
        </button>
      </div>
      <InvestigationResults request={request} onFollowUp={handleFollowUp} />
    </main>
  );
}

function ConfigurationPage() {
  return (
    <main className="animate-fade-in" style={{ width: '100%' }}>
      <ConfigurationPanel />
    </main>
  );
}

function IncidentsPage() {
  const navigate = useNavigate();
  return (
    <main className="animate-fade-in" style={{ width: '100%' }}>
      <IncidentsPanel
        onInvestigate={(prefill) => navigate('/', { state: { prefill } })}
      />
    </main>
  );
}
