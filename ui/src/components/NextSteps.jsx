import { useState } from 'react';
import { runInvestigationTool } from '../api';

/**
 * Suggested next steps, with the means to carry them out.
 *
 * A suggestion the reader has to go and perform by hand is a dead end at exactly
 * the moment the investigation was getting somewhere. Two kinds of step can be
 * acted on here:
 *
 *   tool          — re-runs one tool against the evidence already gathered.
 *                   Instant, no model call, and scoped to the same window the
 *                   conclusion came from.
 *   investigation — hands the step back to the agent as a fresh question.
 *
 * Anything the backend could not make executable is still listed, just without
 * a button, rather than being hidden.
 */
export default function NextSteps({ steps, investigationId, onInvestigate }) {
  if (!steps?.length) return null;

  return (
    <div className="li-section" style={{ marginTop: '1.5rem' }}>
      <h4>Suggested next steps</h4>
      <ul className="li-steps">
        {steps.map((step, i) => (
          <StepRow key={i} step={step} investigationId={investigationId}
            onInvestigate={onInvestigate} />
        ))}
      </ul>
    </div>
  );
}

function StepRow({ step, investigationId, onInvestigate }) {
  const [state, setState] = useState('idle');   // idle | running | done | failed
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(false);

  const runTool = async () => {
    setState('running'); setError(null);
    try {
      const data = await runInvestigationTool(investigationId, step.tool, step.tool_input);
      setResult(data);
      setState('done');
      setOpen(true);
    } catch (err) {
      setError(err.message);
      setState('failed');
    }
  };

  const canRunTool = step.kind === 'tool' && step.tool && investigationId;
  const canInvestigate = step.kind === 'investigation' && step.question && onInvestigate;

  return (
    <li className="li-step">
      <div className="li-step-main">
        <div className="li-step-text">
          <div className="li-step-label">{step.label}</div>
          {step.reason && <div className="li-step-reason">{step.reason}</div>}
          {step.tool && (
            <code className="li-step-tool">
              {step.tool}({formatArgs(step.tool_input)})
            </code>
          )}
        </div>

        <div className="li-step-actions">
          {canRunTool && (
            <button type="button" className="li-btn li-btn--primary li-step-button"
              onClick={runTool} disabled={state === 'running'}>
              {state === 'running' ? 'Running…' : state === 'done' ? 'Run again' : 'Run'}
            </button>
          )}
          {canInvestigate && (
            <button type="button" className="li-btn li-step-button"
              onClick={() => onInvestigate(step.question)}>
              Investigate
            </button>
          )}
          {!canRunTool && !canInvestigate && (
            <span className="li-muted li-step-manual">manual</span>
          )}
        </div>
      </div>

      {state === 'failed' && (
        <div className="li-step-result li-step-result--error">{error}</div>
      )}

      {state === 'done' && result && (
        <div className="li-step-result">
          <button type="button" className="li-raw-button" onClick={() => setOpen(!open)}>
            {open ? 'Hide result' : 'Show result'}
            {result.evidence_ids?.length > 0 && (
              <span className="li-muted"> · {result.evidence_ids.length} evidence id
                {result.evidence_ids.length > 1 ? 's' : ''}</span>
            )}
          </button>
          {open && (
            <>
              {result.table?.rows?.length > 0 ? (
                <div className="li-table-wrap" style={{ marginTop: 10 }}>
                  <table className="li-table">
                    <thead>
                      <tr>{result.table.columns.map((c) => <th key={c}>{c}</th>)}</tr>
                    </thead>
                    <tbody>
                      {result.table.rows.map((row, i) => (
                        <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <pre className="li-raw-pre">{result.observation}</pre>
              )}
            </>
          )}
        </div>
      )}
    </li>
  );
}

function formatArgs(input) {
  return Object.entries(input || {})
    .filter(([, v]) => v !== '' && v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(', ');
}
