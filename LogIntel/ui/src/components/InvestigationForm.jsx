import { useEffect, useState } from 'react';
import { getSystems } from '../api';
import { useToast } from '../toast';

// Must match app/models/plan.py ALLOWED_DURATIONS — the orchestrator rejects
// anything else, so the picker only ever offers values the backend accepts.
const DURATIONS = ['auto', '15m', '30m', '1h', '3h', '6h', '12h', '24h', '3d', '7d'];

export default function InvestigationForm({ onSubmit, initial, lockedSystem }) {
  const toast = useToast();
  const [systems, setSystems] = useState([]);
  
  const [form, setForm] = useState({
    system_id: lockedSystem?.id || initial?.system_id || '',
    environment: lockedSystem?.environments?.[0] || initial?.environment || '',
    service: initial?.service || '',
    question: initial?.question || 'Something is wrong. What is the root cause?',
    duration: initial?.duration || 'auto',
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
      duration: form.duration === 'auto' ? undefined : form.duration,
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

        <div className="row" style={{ alignItems: 'flex-end', flexWrap: 'nowrap' }}>
          <div className="field" style={{ flex: 1 }}>
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

          <div className="field" style={{ flex: 1 }}>
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

          <div className="field" style={{ width: 92, flex: 'none' }}>
            <label htmlFor="li-duration">Window</label>
            <select
              id="li-duration"
              className="input"
              value={form.duration}
              onChange={(e) => setForm((f) => ({ ...f, duration: e.target.value }))}
            >
              {DURATIONS.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
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
          Analyse
        </button>
        <span className="hint">
          The plan is validated against what exists — it can only pick a service
          and environment from these lists.
        </span>
      </div>
    </form>
  );
}
