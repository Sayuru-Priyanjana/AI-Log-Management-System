import { useState } from 'react';
import { usePreferences } from '../preferences';

// Small building blocks shared by every panel, so "readable" means the same
// thing everywhere: a severity chip is always the same four colors, a
// confidence bar always reads the same way, and the escape hatch to raw JSON
// always looks and behaves identically.

// Severity and tone map onto the shared chip classes rather than carrying their
// own colour table. The old one hardcoded pastels tuned for a dark background,
// which dropped to ~2:1 the moment the light theme existed.
const SEVERITY_CLASS = {
  critical: 'chip--err', fatal: 'chip--err', error: 'chip--err',
  high: 'chip--err',
  warn: 'chip--warn', warning: 'chip--warn', medium: 'chip--warn',
  low: '', info: '',
};

export function SeverityChip({ level }) {
  const key = String(level || 'info').toLowerCase();
  return <span className={`li-chip ${SEVERITY_CLASS[key] ?? ''}`}>{key.toUpperCase()}</span>;
}

const TONE_CLASS = { neutral: '', accent: 'chip--accent', good: 'chip--ok', bad: 'chip--err' };

export function Chip({ children, tone = 'neutral' }) {
  return <span className={`li-chip ${TONE_CLASS[tone] ?? ''}`}>{children}</span>;
}

export function ConfidenceBar({ value = 0, label = 'Confidence' }) {
  const pct = Math.round((value || 0) * 100);
  const color = pct >= 70 ? 'var(--ok)' : pct >= 40 ? 'var(--warn)' : 'var(--err)';
  return (
    <div className="li-confidence">
      <div className="li-confidence-label">
        <span>{label}</span>
        <span style={{ color, fontWeight: 700 }}>{pct}%</span>
      </div>
      <div className="li-confidence-track">
        <div className="li-confidence-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

export function ScoreBar({ value = 0 }) {
  const pct = Math.round((value || 0) * 100);
  return (
    <div className="li-score-track" title={`score ${value.toFixed(2)}`}>
      <div className="li-score-fill" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Stat({ label, value, tone }) {
  return (
    <div className={`li-stat ${tone ? `li-stat--${tone}` : ''}`}>
      <div className="li-stat-value">{value}</div>
      <div className="li-stat-label">{label}</div>
    </div>
  );
}

export function EmptyNote({ children }) {
  return <p className="li-empty">{children}</p>;
}

/** The one error-banner treatment used everywhere in the app, so a broken
 * OpenSearch connection and a broken incident controller look like the same
 * kind of problem, not two different UI languages. */
export function Timestamp({ value }) {
  // The agent's zone, not the browser's: the agent writes times into its own
  // prose, and a page formatting in a different one would hand the reader two
  // clocks to reconcile.
  const { formatStamp } = usePreferences();
  if (!value) return <span className="li-empty">unknown time</span>;
  return <span className="li-mono">{formatStamp(value, String(value))}</span>;
}

/** The transparency escape hatch: every panel gets one of these so nothing is
 * ever hidden behind the readable view — you can always see exactly what the
 * backend sent. */
export function RawToggle({ data, label = 'View raw JSON' }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="li-raw-toggle">
      <button type="button" className="li-raw-button" onClick={() => setOpen((v) => !v)}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
          style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 0.2s' }}>
          <path d="M9 18l6-6-6-6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {label}
      </button>
      {open && <pre className="li-raw-pre">{JSON.stringify(data, null, 2)}</pre>}
    </div>
  );
}

export function Section({ title, badge, children, right }) {
  return (
    <div className="li-section">
      <div className="li-section-head">
        <div className="li-section-title">
          <h4>{title}</h4>
          {badge}
        </div>
        {right}
      </div>
      {children}
    </div>
  );
}
