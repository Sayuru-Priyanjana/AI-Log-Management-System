import { useEffect, useState } from 'react';
import { getSystems } from '../api';
import { ErrorBanner } from './common';

// Must match app/models/plan.py ALLOWED_DURATIONS — the orchestrator rejects
// anything else, so the picker only ever offers values the backend accepts.
const DURATIONS = ['auto', '15m', '30m', '1h', '3h', '6h', '12h', '24h', '3d', '7d'];

export default function InvestigationForm({ onSubmit, initial }) {
  const [systems, setSystems] = useState([]);
  const [loadError, setLoadError] = useState(null);
  const [form, setForm] = useState({
    system_id: initial?.system_id || '',
    environment: initial?.environment || '',
    service: initial?.service || '',
    question: initial?.question || 'Something is wrong. What is the root cause?',
    duration: initial?.duration || 'auto',
  });

  useEffect(() => {
    let mounted = true;
    getSystems()
      .then((res) => {
        if (!mounted) return;
        setSystems(res.systems || []);
        if (!form.system_id && res.systems?.length) {
          const first = res.systems[0];
          setForm((f) => ({ ...f, system_id: first.id, environment: first.environments?.[0] || '' }));
        }
      })
      .catch((err) => mounted && setLoadError(err.message));
    return () => { mounted = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedSystem = systems.find((s) => s.id === form.system_id);

  const handleSystemChange = (system_id) => {
    const system = systems.find((s) => s.id === system_id);
    setForm((f) => ({
      ...f, system_id, service: '',
      environment: system?.environments?.[0] || f.environment,
    }));
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!form.system_id || !form.question.trim()) return;
    onSubmit({
      system_id: form.system_id,
      environment: form.environment,
      question: form.question,
      service_hint: form.service || undefined,
      duration: form.duration === 'auto' ? undefined : form.duration,
    });
  };

  return (
    <div className="glass-panel li-form-card">
      <h2>Start Investigation</h2>
      <p className="li-muted li-form-intro">
        The orchestrator turns this into a plan validated against what actually exists —
        it can only pick a system, service and environment from the list below.
      </p>

      {loadError && (
        <div style={{ marginBottom: '1.25rem' }}>
          <ErrorBanner>Could not load systems: {loadError}. Is the agent running?</ErrorBanner>
        </div>
      )}

      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div className="li-field">
          <label className="li-label" htmlFor="li-system">System</label>
          <select
            id="li-system"
            className="glass-input"
            value={form.system_id}
            onChange={(e) => handleSystemChange(e.target.value)}
            required
          >
            {systems.length === 0 && <option value="">No systems discovered yet</option>}
            {systems.map((s) => (
              <option key={s.id} value={s.id}>{s.name} ({s.id}) — {s.services?.length || 0} services</option>
            ))}
          </select>
        </div>

        <div className="li-field-row">
          <div className="li-field">
            <label className="li-label" htmlFor="li-environment">Environment</label>
            <select
              id="li-environment"
              className="glass-input"
              value={form.environment}
              onChange={(e) => setForm((f) => ({ ...f, environment: e.target.value }))}
              required
            >
              {(selectedSystem?.environments || []).map((env) => (
                <option key={env} value={env}>{env}</option>
              ))}
            </select>
          </div>

          <div className="li-field">
            <label className="li-label" htmlFor="li-service">
              Service <span className="li-label-optional">(optional)</span>
            </label>
            <select
              id="li-service"
              className="glass-input"
              value={form.service}
              onChange={(e) => setForm((f) => ({ ...f, service: e.target.value }))}
            >
              <option value="">Let the orchestrator decide</option>
              {(selectedSystem?.services || []).map((svc) => (
                <option key={svc.name} value={svc.name}>{svc.name}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="li-field">
          <label className="li-label" htmlFor="li-question">Question</label>
          <textarea
            id="li-question"
            value={form.question}
            onChange={(e) => setForm((f) => ({ ...f, question: e.target.value }))}
            className="glass-input"
            rows="3"
            style={{ resize: 'vertical' }}
            required
          />
        </div>

        <div className="li-field">
          <label className="li-label" htmlFor="li-duration">
            Time window <span className="li-label-optional">(auto lets the plan choose based on your question)</span>
          </label>
          <select
            id="li-duration"
            className="glass-input"
            value={form.duration}
            onChange={(e) => setForm((f) => ({ ...f, duration: e.target.value }))}
          >
            {DURATIONS.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </div>

        <button type="submit" className="glass-button" style={{ marginTop: '0.5rem' }} disabled={!form.system_id}>
          Analyze
        </button>
      </form>
    </div>
  );
}
