import { useState } from 'react';
import Collapsible from './Collapsible';

/**
 * The agent's working, shown as it happens.
 *
 * This is the transparency the whole design rests on: every thought, every tool
 * call with its exact arguments, and every observation it got back. If the final
 * answer looks wrong, the step where it went wrong is visible here rather than
 * having to be guessed at.
 */
export default function ReasoningTrace({ steps, live }) {
  if (!steps.length) return null;

  const toolCalls = steps.filter((s) => s.type === 'action').length;
  const thoughts = steps.filter((s) => s.type === 'thought').length;

  return (
    <Collapsible
      title="Reasoning trace"
      summary={`${thoughts} thought${thoughts === 1 ? '' : 's'} · ${toolCalls} tool call${toolCalls === 1 ? '' : 's'}`}
      right={live ? <span className="li-trace-live">running</span> : null}
      // Open while it is still running: watching the loop work is the point of
      // streaming it. Once there is an answer the working folds away, because
      // the conclusion is what the reader came for.
      defaultOpen={Boolean(live)}
    >
      <ol className="li-trace-list">
        {steps.map((step, i) => <TraceStep key={i} step={step} />)}
      </ol>
    </Collapsible>
  );
}

function TraceStep({ step }) {
  // The seeded signals are the measured facts the whole answer should rest on,
  // so they start expanded rather than hidden behind a "show 6 more lines".
  const [open, setOpen] = useState(Boolean(step.automatic));

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
      <li className={`li-trace-item li-trace-item--observation${
        step.automatic ? ' li-trace-item--seeded' : ''}`}>
        <span className="li-trace-marker">←</span>
        <div style={{ minWidth: 0 }}>
          <div className="li-trace-kind">
            observed
            {/* Saying "observed" alone would imply the agent asked for this.
                It did not — the measured signals are put in front of it before
                it chooses anything, and the trace has to say so. */}
            {step.automatic && (
              <span className="li-trace-seeded-tag" title={
                'Measured signals from logs, Kubernetes events and metrics. Supplied '
                + 'automatically so the agent cannot answer without having seen them.'}>
                supplied automatically
              </span>
            )}
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
