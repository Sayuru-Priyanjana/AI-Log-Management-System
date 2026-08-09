import { useEffect, useState } from 'react';
import { getIncidents, resetIncidents, startIncident, stopIncident } from '../api';
import { Chip, ErrorBanner } from './common';

function useElapsed(startedAt) {
  const [, force] = useState(0);
  useEffect(() => {
    if (!startedAt) return undefined;
    const id = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [startedAt]);
  if (!startedAt) return null;
  return Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000));
}

function ScenarioCard({ id, spec, activeSince, busy, onStart, onStop, onInvestigate }) {
  const elapsed = useElapsed(activeSince);
  const isActive = !!activeSince;
  const settled = isActive && elapsed !== null && elapsed >= spec.settle_seconds;

  return (
    <div className={`li-card li-incident-card ${isActive ? 'li-incident-card--active' : ''}`}>
      <div className="li-card-head">
        <strong className="li-card-title">{spec.title}</strong>
        <span className="li-spacer" />
        {isActive && (
          <Chip tone={settled ? 'good' : 'accent'}>
            {settled ? `active ${elapsed}s — ready to investigate` : `developing… ${elapsed}s / ~${spec.settle_seconds}s`}
          </Chip>
        )}
      </div>
      <p className="li-body-text" style={{ margin: '8px 0' }}>{spec.description}</p>
      <div className="li-chiprow">
        <Chip>expects: {spec.expected_cause.replace(/_/g, ' ')}</Chip>
        {spec.expected_service && <Chip tone="accent">{spec.expected_service}</Chip>}
        {(spec.expected_signals || []).map((s) => <Chip key={s}>{s.replace(/_/g, ' ')}</Chip>)}
      </div>
      <div className="li-incident-actions">
        {!isActive ? (
          <button type="button" className="li-btn li-btn--primary" disabled={busy} onClick={() => onStart(id)}>
            {busy ? 'Starting…' : 'Start incident'}
          </button>
        ) : (
          <>
            <button type="button" className="li-btn" disabled={busy} onClick={() => onStop(id)}>
              {busy ? 'Stopping…' : 'Stop'}
            </button>
            <button
              type="button"
              className="li-btn li-btn--primary"
              disabled={!settled}
              onClick={() => onInvestigate(spec, id)}
              title={settled ? '' : `Give it ~${spec.settle_seconds}s to develop first`}
            >
              Investigate now
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export default function IncidentsPanel({ onInvestigate }) {
  const [catalogue, setCatalogue] = useState(null);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const refresh = async () => {
    try {
      const data = await getIncidents();
      setCatalogue(data);
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 8000);
    return () => clearInterval(id);
  }, []);

  const withBusy = async (id, fn) => {
    setBusyId(id);
    try {
      await fn();
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  };

  const handleStart = (id) => withBusy(id, () => startIncident(id));
  const handleStop = (id) => withBusy(id, () => stopIncident(id));
  const handleResetAll = () => withBusy('*', () => resetIncidents());

  const handleInvestigate = (spec) => {
    // The testbed's namespace and its system_id are the same string
    // ("shopdemo") by convention — see testbed/manifests labels.
    onInvestigate({
      system_id: catalogue?.namespace,
      question: `${spec.title}. What is the root cause?`,
      service_hint: spec.expected_service,
    });
  };

  if (error && !catalogue) {
    return (
      <ErrorBanner>
        <strong>Cannot reach the incident injector.</strong> {error}
        <p className="li-muted" style={{ marginTop: 8 }}>
          Check that the testbed VM is up: <code className="li-mono">cd testbed &amp;&amp; vagrant status</code>
        </p>
      </ErrorBanner>
    );
  }

  if (!catalogue) return <p className="li-muted">Loading scenarios…</p>;

  const scenarios = Object.entries(catalogue.scenarios || {});
  const activeCount = Object.keys(catalogue.active || {}).length;

  return (
    <div className="li-incidents">
      <div className="glass-panel li-incidents-header">
        <div>
          <h3>Inject a real failure into <span className="text-gradient">{catalogue.namespace}</span></h3>
          <p className="li-muted">
            Each scenario patches the live cluster the same way an eval run does — real pods,
            real Prometheus metrics, real Kubernetes events. Nothing here is simulated data.
          </p>
        </div>
        <button type="button" className="li-btn" disabled={activeCount === 0 || busyId === '*'} onClick={handleResetAll}>
          {busyId === '*' ? 'Resetting…' : `Reset all (${activeCount} active)`}
        </button>
      </div>

      {error && <div style={{ marginBottom: 16 }}><ErrorBanner>{error}</ErrorBanner></div>}

      <div className="li-incident-grid">
        {scenarios.map(([id, spec]) => (
          <ScenarioCard
            key={id}
            id={id}
            spec={spec}
            activeSince={catalogue.active?.[id]}
            busy={busyId === id}
            onStart={handleStart}
            onStop={handleStop}
            onInvestigate={handleInvestigate}
          />
        ))}
      </div>
    </div>
  );
}
