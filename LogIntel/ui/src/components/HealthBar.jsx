import { useEffect, useState } from 'react';
import { getHealth } from '../api';

const LABELS = {
  opensearch: 'OpenSearch',
  prometheus: 'Prometheus',
  model: 'Model',
  incident_controller: 'Testbed',
  registry: 'Registry',
};

const ORDER = ['opensearch', 'prometheus', 'model', 'incident_controller', 'registry'];

function colorFor(status) {
  if (status === 'ok') return 'var(--success)';
  if (status === 'degraded' || status === 'partial' || status === 'empty') return 'var(--warning)';
  return 'var(--error)';
}

/**
 * Always-visible connectivity strip. This is "transparency" in its most literal
 * form: every dependency the pipeline touches, its live status, and — on
 * hover — exactly what's wrong and how to fix it, refreshed every 15s.
 */
export default function HealthBar() {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let mounted = true;
    const poll = async () => {
      try {
        const data = await getHealth();
        if (mounted) { setHealth(data); setError(null); }
      } catch (err) {
        if (mounted) { setError(err.message); setHealth(null); }
      }
    };
    poll();
    const id = setInterval(poll, 15000);
    return () => { mounted = false; clearInterval(id); };
  }, []);

  if (error) {
    return (
      <div className="li-healthbar li-healthbar--down">
        <span className="li-healthbar-dot" style={{ color: 'var(--error)' }} />
        Cannot reach the agent at localhost:8000 — is it running?
      </div>
    );
  }

  if (!health) return null;

  return (
    <div className="li-healthbar">
      {ORDER.filter((key) => health.components[key]).map((key) => {
        const c = health.components[key];
        const title = [c.url, c.model, c.hint, c.error, ...(c.problems || [])]
          .filter(Boolean).join('\n');
        return (
          <div key={key} className="li-healthbar-item" title={title}>
            <span className="li-healthbar-dot" style={{ color: colorFor(c.status) }} />
            <span>{LABELS[key] || key}</span>
          </div>
        );
      })}
    </div>
  );
}
