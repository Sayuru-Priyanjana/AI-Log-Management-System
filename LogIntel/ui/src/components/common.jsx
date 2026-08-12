import { useState } from 'react';
import { usePreferences } from '../preferences';

// Small building blocks shared by every panel, so "readable" means the same
// thing everywhere: a severity chip is always the same four colors, a
// confidence bar always reads the same way, and the escape hatch to raw JSON
// always looks and behaves identically.

const SEVERITY_COLORS = {
  critical: { fg: '#fca5a5', bg: 'rgba(239,68,68,0.15)', border: 'rgba(239,68,68,0.4)' },
  high: { fg: '#fdba74', bg: 'rgba(249,115,22,0.15)', border: 'rgba(249,115,22,0.4)' },
  medium: { fg: '#fde68a', bg: 'rgba(245,158,11,0.15)', border: 'rgba(245,158,11,0.4)' },
  low: { fg: '#93c5fd', bg: 'rgba(59,130,246,0.15)', border: 'rgba(59,130,246,0.4)' },
  info: { fg: '#cbd5e1', bg: 'rgba(148,163,184,0.15)', border: 'rgba(148,163,184,0.4)' },
  error: { fg: '#fca5a5', bg: 'rgba(239,68,68,0.15)', border: 'rgba(239,68,68,0.4)' },
  fatal: { fg: '#fca5a5', bg: 'rgba(239,68,68,0.15)', border: 'rgba(239,68,68,0.4)' },
  warn: { fg: '#fde68a', bg: 'rgba(245,158,11,0.15)', border: 'rgba(245,158,11,0.4)' },
  warning: { fg: '#fde68a', bg: 'rgba(245,158,11,0.15)', border: 'rgba(245,158,11,0.4)' },
};

export function SeverityChip({ level }) {
  const key = String(level || 'info').toLowerCase();
  const c = SEVERITY_COLORS[key] || SEVERITY_COLORS.info;
  return (
    <span
      className="li-chip"
      style={{ color: c.fg, background: c.bg, border: `1px solid ${c.border}` }}
    >
      {String(level || 'info').toUpperCase()}
    </span>
  );
}

export function Chip({ children, tone = 'neutral' }) {
  const tones = {
    neutral: { fg: 'var(--text-secondary)', bg: 'rgba(255,255,255,0.06)', border: 'var(--glass-border)' },
    accent: { fg: 'var(--accent-color)', bg: 'rgba(56,189,248,0.12)', border: 'rgba(56,189,248,0.35)' },
    good: { fg: '#6ee7b7', bg: 'rgba(16,185,129,0.12)', border: 'rgba(16,185,129,0.35)' },
    bad: { fg: '#fca5a5', bg: 'rgba(239,68,68,0.12)', border: 'rgba(239,68,68,0.35)' },
  };
  const c = tones[tone] || tones.neutral;
  return (
    <span className="li-chip" style={{ color: c.fg, background: c.bg, border: `1px solid ${c.border}` }}>
      {children}
    </span>
  );
}

export function ConfidenceBar({ value = 0, label = 'Confidence' }) {
  const pct = Math.round((value || 0) * 100);
  const color = pct >= 70 ? '#10b981' : pct >= 40 ? '#f59e0b' : '#ef4444';
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
export function ErrorBanner({ children }) {
  return (
    <div className="glass-panel li-error-banner">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
        style={{ flexShrink: 0, marginTop: 2 }}>
        <path d="M12 9v4M12 17h.01M10.3 3.9L2.7 17a2 2 0 001.7 3h15.2a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"
          strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div>{children}</div>
    </div>
  );
}

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
