import { useState } from 'react';
import HealthBar from './components/HealthBar';
import IncidentsPanel from './components/IncidentsPanel';
import InvestigationForm from './components/InvestigationForm';
import InvestigationResults from './components/InvestigationResults';
import './index.css';

const TABS = [
  { id: 'investigate', label: 'Investigate' },
  { id: 'incidents', label: 'Incidents' },
];

function App() {
  const [tab, setTab] = useState('investigate');
  const [request, setRequest] = useState(null);
  const [prefill, setPrefill] = useState(null);

  const handleStart = (data) => setRequest(data);
  const handleReset = () => { setRequest(null); setPrefill(null); };

  const handleInvestigateFromIncident = (fields) => {
    setRequest(null);
    setPrefill(fields);
    setTab('investigate');
  };

  return (
    <>
      <div className="bg-mesh" />
      <div className="container" style={{ position: 'relative', zIndex: 1 }}>
        <header className="li-header animate-fade-in">
          <div className="li-brand">
            <span className="li-brand-mark">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
                <path d="M9 12l2 2 4-4M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
            <h1>Log<span className="text-gradient">Intel</span></h1>
          </div>
          <p>Deterministic evidence extraction with a narrowly-scoped LLM on top.</p>
          <HealthBar />
        </header>

        <nav className="li-tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`li-tab ${tab === t.id ? 'li-tab--active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>

        <main className="flex flex-col gap-6 items-center" style={{ width: '100%' }}>
          {tab === 'investigate' && (
            !request ? (
              <div className="animate-slide-in" style={{ width: '100%', maxWidth: '600px' }}>
                <InvestigationForm onSubmit={handleStart} initial={prefill} />
              </div>
            ) : (
              <div className="animate-slide-in" style={{ width: '100%' }}>
                <div className="li-page-toolbar">
                  <h2>Investigation</h2>
                  <button className="li-btn" onClick={handleReset}>
                    New Investigation
                  </button>
                </div>
                <InvestigationResults request={request} />
              </div>
            )
          )}

          {tab === 'incidents' && (
            <div className="animate-slide-in" style={{ width: '100%' }}>
              <IncidentsPanel onInvestigate={handleInvestigateFromIncident} />
            </div>
          )}
        </main>
      </div>
    </>
  );
}

export default App;
