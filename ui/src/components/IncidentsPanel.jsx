import { useEffect, useState } from 'react';
import { getIncidents, resetIncidents, startIncident, stopIncident } from '../api';
import { Chip } from './common';
import { useToast } from '../toast';

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
  const toast = useToast();
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
      toast.error('Scenario command failed', { detail: err.message });
    } finally {
      setBusyId(null);
    }
  };

  const handleStart = (id) => withBusy(id, async () => {
    await startIncident(id);
    toast.success(`Started ${id}`, { detail: 'Give it time to develop before investigating.' });
  });
  const handleStop = (id) => withBusy(id, async () => {
    await stopIncident(id);
    toast.success(`Stopped ${id}`);
  });
  const handleResetAll = () => withBusy('*', async () => {
    await resetIncidents();
    toast.success('All scenarios reset');
  });

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
      <div className="page">
        <div className="empty">
          The incident injector is not answering ({error}).<br />
          Check the testbed VM: <code>cd testbed &amp;&amp; vagrant status</code>
        </div>
      </div>
    );
  }

  if (!catalogue) return <div className="page"><div className="empty">Loading scenarios…</div></div>;

  const scenarios = Object.entries(catalogue.scenarios || {});
  const activeCount = Object.keys(catalogue.active || {}).length;

  return (
    <div className="page">
      <div className="pagehead">
        <h2>Incidents</h2>
        <p>
          Each scenario patches <strong>{catalogue.namespace}</strong> for real — real pods,
          real metrics, real events. Nothing here is simulated.
        </p>
        <span className="spacer" />
        <button type="button" className="li-btn" disabled={activeCount === 0 || busyId === '*'} onClick={handleResetAll}>
          {busyId === '*' ? 'Resetting…' : `Reset all (${activeCount} active)`}
        </button>
      </div>

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
