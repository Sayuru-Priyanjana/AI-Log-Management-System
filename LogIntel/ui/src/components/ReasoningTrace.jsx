import { useState } from 'react';

/**
 * The agent's working, shown as it happens.
 *
 * This is the transparency the whole design rests on: every thought, every tool
 * call with its exact arguments, and every observation it got back. If the final
 * answer looks wrong, the step where it went wrong is visible here rather than
 * having to be guessed at.
 */
export default function ReasoningTrace({ steps, live }) {
  const [collapsed, setCollapsed] = useState(false);
  if (!steps.length) return null;

  const toolCalls = steps.filter((s) => s.type === 'action').length;

  return (
    <div className="glass-panel li-trace">
      <button type="button" className="li-trace-head" onClick={() => setCollapsed(!collapsed)}>
        <span className="li-trace-title">Reasoning trace</span>
        <span className="li-muted">
          {steps.filter((s) => s.type === 'thought').length} thought
          {toolCalls === 1 ? '' : 's'} · {toolCalls} tool call{toolCalls === 1 ? '' : 's'}
        </span>
        <span className="li-spacer" />
        {live && <span className="li-trace-live">running</span>}
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2.4"
          style={{ transform: collapsed ? 'none' : 'rotate(180deg)', transition: 'transform .2s' }}>
          <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {!collapsed && (
        <ol className="li-trace-list">
          {steps.map((step, i) => <TraceStep key={i} step={step} />)}
        </ol>
      )}
    </div>
  );
}

function TraceStep({ step }) {
  const [open, setOpen] = useState(false);

  if (step.type === 'thought') {
    return (
      <li className="li-trace-item li-trace-item--thought">
        <span className="li-trace-marker">{step.step}</span>
        <div>
          <div className="li-trace-kind">thought</div>
          <div className="li-trace-text">{step.text}</div>
        </div>
      </li>
    );
  }

  if (step.type === 'action') {
    const args = Object.entries(step.input || {})
      .filter(([, v]) => v !== '' && v !== null && v !== undefined)
      .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
      .join(', ');
    return (
      <li className="li-trace-item li-trace-item--action">
        <span className="li-trace-marker">→</span>
        <div>
          <div className="li-trace-kind">called</div>
          <div className="li-trace-tool">
            <code>{step.tool}({args})</code>
          </div>
        </div>
      </li>
    );
  }

  if (step.type === 'observation') {
    const lines = (step.text || '').split('\n');
    const head = lines[0];
    const rest = lines.slice(1);
    const ids = step.evidence_ids || [];
    return (
      <li className="li-trace-item li-trace-item--observation">
        <span className="li-trace-marker">←</span>
        <div style={{ minWidth: 0 }}>
          <div className="li-trace-kind">
            observed
            {ids.length > 0 && (
              <span className="li-muted"> · {ids.length} evidence id{ids.length > 1 ? 's' : ''}</span>
            )}
          </div>
          <div className="li-trace-text">{head}</div>
          {rest.length > 0 && (
            <>
              <button type="button" className="li-raw-button" onClick={() => setOpen(!open)}
                style={{ marginTop: 8 }}>
                {open ? 'Hide' : `Show ${rest.length} more line${rest.length > 1 ? 's' : ''}`}
              </button>
              {open && <pre className="li-raw-pre">{rest.join('\n')}</pre>}
            </>
          )}
        </div>
      </li>
    );
  }

  // note / exhausted / error
  return (
    <li className="li-trace-item li-trace-item--note">
      <span className="li-trace-marker">!</span>
      <div>
        <div className="li-trace-kind">{step.type}</div>
        <div className="li-trace-text">{step.message}</div>
      </div>
    </li>
  );
}
