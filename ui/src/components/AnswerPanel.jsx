import { useState } from 'react';
import NextSteps from './NextSteps';

const MODE_LABEL = {
  root_cause: 'Root cause',
  data_extraction: 'Records found',
  aggregation: 'Breakdown',
  health_check: 'Health check',
  explanation: 'Explanation',
};

function confidenceTone(value) {
  if (value >= 0.7) return { color: 'var(--success)', word: 'well supported' };
  if (value >= 0.45) return { color: 'var(--warning)', word: 'partly supported' };
  return { color: 'var(--error)', word: 'weakly supported' };
}

/**
 * The verified answer.
 *
 * Everything the pipeline checked is shown rather than summarised away: which
 * citations resolved and which did not, what raised and lowered the confidence,
 * and which reasoning steps rest on nothing. A reader who can see the weak parts
 * can decide how far to trust the strong ones.
 */
export default function AnswerPanel({ answer, investigationId, onInvestigate }) {
  if (!answer) return null;

  const tone = confidenceTone(answer.confidence);
  const unresolved = (answer.citations || []).filter((c) => c.status === 'unresolved');
  const unsupported = (answer.reasoning || []).filter(
    (s) => s.kind !== 'observation' && !(s.evidence_ids || []).length,
  );

  return (
    <div className="li-answer animate-fade-in">
      <div className="li-answer-head">
        <span className="li-chip li-chip--mode">{MODE_LABEL[answer.mode] || answer.mode}</span>
        {answer.root_cause_service && (
          <span className="li-chip li-chip--service">{answer.root_cause_service}</span>
        )}
        <span className="li-spacer" />
        <ConfidenceMeter value={answer.confidence} tone={tone}
          factors={answer.confidence_factors} />
      </div>

      <h2 className="li-answer-headline">{answer.headline}</h2>
      {answer.detail && <p className="li-answer-detail">{answer.detail}</p>}

      {answer.warning_analysis && (
        <Section title="Warning Analysis">
          <p className="li-answer-detail" style={{ marginTop: 0 }}>{answer.warning_analysis}</p>
        </Section>
      )}

      {(unresolved.length > 0 || unsupported.length > 0) && (
        <div className="li-answer-warning">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2.2" style={{ flexShrink: 0, marginTop: 2 }}>
            <path d="M12 9v4M12 17h.01M10.3 3.9L2.7 17a2 2 0 001.7 3h15.2a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"
              strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <div>
            {unresolved.length > 0 && (
              <div>{unresolved.length} citation{unresolved.length > 1 ? 's' : ''} pointed at
                evidence that does not exist and {unresolved.length > 1 ? 'were' : 'was'} rejected.</div>
            )}
            {unsupported.length > 0 && (
              <div>{unsupported.length} reasoning step{unsupported.length > 1 ? 's' : ''} cite
                no evidence.</div>
            )}
          </div>
        </div>
      )}

      {answer.table && <DataTable table={answer.table} />}

      {answer.reasoning?.length > 0 && (
        <Section title="How it reached this">
          <ol className="li-reasoning">
            {answer.reasoning.map((step, i) => (
              <li key={i} className={step.evidence_ids?.length ? '' : 'li-reasoning--bare'}>
                <div className="li-reasoning-claim">
                  <span className={`li-kind li-kind--${step.kind || 'inference'}`}>
                    {step.kind || 'inference'}
                  </span>
                  {step.claim}
                </div>
                {step.because && (
                  <div className="li-reasoning-because">because {step.because}</div>
                )}
                <EvidenceRow ids={step.evidence_ids} citations={answer.citations} />
              </li>
            ))}
          </ol>
        </Section>
      )}

      {answer.assumptions?.length > 0 && (
        <Section title="Assumed, not proven">
          <ul className="li-assumptions">
            {answer.assumptions.map((a, i) => (
              <li key={i}>
                <strong>{a.statement}</strong>
                {a.basis && <div className="li-muted">Basis: {a.basis}</div>}
                {a.impact_if_wrong && (
                  <div className="li-assumption-risk">If wrong: {a.impact_if_wrong}</div>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {answer.limitations?.length > 0 && (
        <Section title="What this does not establish">
          <ul className="li-notelist">
            {answer.limitations.map((l, i) => <li key={i}>{l}</li>)}
          </ul>
        </Section>
      )}

      <NextSteps steps={answer.next_steps} investigationId={investigationId}
        onInvestigate={onInvestigate} />
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div className="li-section" style={{ marginTop: '1.5rem' }}>
      <h4>{title}</h4>
      {children}
    </div>
  );
}

function EvidenceRow({ ids, citations }) {
  if (!ids?.length) {
    return <div className="li-evidence-row li-evidence-row--empty">no evidence cited</div>;
  }
  const byId = Object.fromEntries((citations || []).map((c) => [c.id, c]));
  return (
    <div className="li-evidence-row">
      {ids.map((id) => {
        const citation = byId[id];
        const bad = citation?.status === 'unresolved';
        return (
          <span key={id}
            className={`li-idchip ${bad ? 'li-idchip--bad' : 'li-idchip--good'}`}
            title={bad ? (citation?.detail || 'Does not resolve') : (citation?.label || id)}>
            {bad ? '✕ ' : ''}{id}
          </span>
        );
      })}
    </div>
  );
}

function ConfidenceMeter({ value, tone, factors }) {
  const [open, setOpen] = useState(false);
  const pct = Math.round((value || 0) * 100);
  return (
    <div className="li-confidence-block">
      <button type="button" className="li-confidence-toggle" onClick={() => setOpen(!open)}
        title="Why this confidence?">
        <span className="li-confidence-value" style={{ color: tone.color }}>{pct}%</span>
        <span className="li-muted">{tone.word}</span>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2.5"
          style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }}>
          <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div className="li-confidence-factors">
          {(factors || []).length === 0 && (
            <div className="li-muted">Nothing adjusted the model's own estimate.</div>
          )}
          {(factors || []).map((f, i) => (
            <div key={i} className={`li-factor li-factor--${f.direction}`}>
              <span>{f.direction === 'raises' ? '▲' : '▼'}</span>
              <span>{f.factor}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DataTable({ table }) {
  if (!table?.rows?.length) return null;
  return (
    <div className="li-section" style={{ marginTop: '1.5rem' }}>
      <div className="li-section-head">
        <h4>{table.query_description || 'Results'}</h4>
        <span className="li-muted">
          {table.total_matched} matched{table.truncated ? ' (showing the first rows)' : ''}
        </span>
      </div>
      <div className="li-table-wrap">
        <table className="li-table">
          <thead>
            <tr>{table.columns.map((c) => <th key={c}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {table.rows.map((row, i) => (
              <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
