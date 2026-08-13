import { useEffect, useState } from 'react';
import { getSystems } from '../api';
import { useToast } from '../toast';

// Removed DURATIONS array since we use explicit start/end times

export default function InvestigationForm({ onSubmit, initial, lockedSystem, submitLabel = 'Analyse' }) {
  const toast = useToast();
  const [systems, setSystems] = useState([]);
  
  const [form, setForm] = useState({
    system_id: lockedSystem?.id || initial?.system_id || '',
    environment: lockedSystem?.environments?.[0] || initial?.environment || '',
    service: initial?.service || '',
    question: initial?.question || 'Something is wrong. What is the root cause?',
    start_time: initial?.start_time || new Date(Date.now() - 3600000).toISOString().slice(0, 16),
    end_time: initial?.end_time || new Date().toISOString().slice(0, 16),
  });

  useEffect(() => {
    // If we are locked to a system and have environments, we don't necessarily need to fetch systems.
    // However, if lockedSystem is provided, we sync the form to it.
    if (lockedSystem) {
      setForm(f => ({
        ...f,
        system_id: lockedSystem.id,
        environment: f.environment || lockedSystem.environments?.[0] || ''
      }));
    }
    
    let mounted = true;
    if (!lockedSystem) {
      getSystems()
        .then((res) => {
          if (!mounted) return;
          setSystems(res.systems || []);
          if (!form.system_id && res.systems?.length) {
            const first = res.systems[0];
            setForm((f) => ({ ...f, system_id: first.id, environment: first.environments?.[0] || '' }));
          } else if (form.system_id && !form.environment && res.systems?.length) {
            const matched = res.systems.find(s => s.id === form.system_id);
            if (matched) {
              setForm((f) => ({ ...f, environment: matched.environments?.[0] || '' }));
            }
          }
        })
        .catch((err) => mounted && toast.error('Could not load systems', { detail: err.message }));
    }
    return () => { mounted = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lockedSystem]);

  const currentSystem = lockedSystem || systems.find((s) => s.id === form.system_id);

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
      start_time: new Date(form.start_time).toISOString(),
      end_time: new Date(form.end_time).toISOString(),
    });
  };

  return (
    <form onSubmit={handleSubmit} className="col">
      {!lockedSystem && (
        <div className="field">
          <label htmlFor="li-system">System</label>
            <select
              id="li-system"
              className="input"
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
        )}

        {/* Two rows rather than one crammed row of four: this form runs both in
            a wide two-column card (Workstation) and a ~280px sidebar (Agent),
            and four flex:1 fields with a forced nowrap simply don't fit the
            narrow case — they overflowed their box and their labels visually
            collided. Each field wraps to its own line below a natural
            min-width instead of overlapping its neighbour. */}
        <div className="row" style={{ alignItems: 'flex-end' }}>
          <div className="field" style={{ flex: 1, minWidth: 110 }}>
            <label htmlFor="li-environment">Environment</label>
            <select
              id="li-environment"
              className="input"
              value={form.environment}
              onChange={(e) => setForm((f) => ({ ...f, environment: e.target.value }))}
              required
            >
              {(currentSystem?.environments || []).map((env) => (
                <option key={env} value={env}>{env}</option>
              ))}
            </select>
          </div>

          <div className="field" style={{ flex: 1, minWidth: 110 }}>
            <label htmlFor="li-service">
              Service <span className="dim">optional</span>
            </label>
            <select
              id="li-service"
              className="input"
              value={form.service}
              onChange={(e) => setForm((f) => ({ ...f, service: e.target.value }))}
            >
              <option value="">Let the orchestrator decide</option>
              {(currentSystem?.services || []).map((svc) => (
                <option key={svc.name} value={svc.name}>{svc.name}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="row" style={{ alignItems: 'flex-end' }}>
          <div className="field" style={{ flex: 1, minWidth: 150 }}>
            <label htmlFor="li-start-time">Start time</label>
            <input
              type="datetime-local"
              id="li-start-time"
              className="input"
              value={form.start_time}
              onChange={(e) => setForm((f) => ({ ...f, start_time: e.target.value }))}
              required
            />
          </div>

          <div className="field" style={{ flex: 1, minWidth: 150 }}>
            <label htmlFor="li-end-time">End time</label>
            <input
              type="datetime-local"
              id="li-end-time"
              className="input"
              value={form.end_time}
              onChange={(e) => setForm((f) => ({ ...f, end_time: e.target.value }))}
              required
            />
          </div>
        </div>

        <div className="field">
          <label htmlFor="li-question">Question</label>
          <textarea
            id="li-question"
            value={form.question}
            onChange={(e) => setForm((f) => ({ ...f, question: e.target.value }))}
            className="input"
            rows="3"
            style={{ resize: 'vertical' }}
            required
          />
        </div>

      <div className="row">
        <button type="submit" className="btn btn--primary" disabled={!form.system_id}>
          {submitLabel}
        </button>
      </div>
      <span className="hint">
        The plan is validated against what exists — it can only pick a service
        and environment from these lists.
      </span>
    </form>
  );
}
