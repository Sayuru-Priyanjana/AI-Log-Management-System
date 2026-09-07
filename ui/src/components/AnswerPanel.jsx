import { useState } from 'react';
import '../answer.css';
import Collapsible from './Collapsible';
import NextSteps from './NextSteps';

/**
 * The answer, and the case for it.
 *
 * The redesign is built around one order, because it is the order a reader
 * actually needs: **what the answer is**, then **how it was reached**, then
 * **what was assumed rather than shown**. Everything else — the citations, the
 * confidence factors, the limitations — hangs off those three.
 *
 * The previous layout put a row of small chips and a percentage above the
 * headline, set the conclusion at roughly the size of the body text, and ran
 * reasoning, assumptions and limitations together as three near-identical
 * lists. Scanning it meant reading it. Here the verdict is typographically
 * unmissable, the reasoning is a numbered chain, and assumptions are visually
 * *unlike* reasoning — amber, flagged, and stating what breaks if they are
 * wrong — because the difference between "this was measured" and "this was
 * assumed" is the single most important distinction on the page.
 */

const MODE_LABEL = {
  root_cause: 'Root cause',
  data_extraction: 'Records found',
  aggregation: 'Breakdown',
  health_check: 'Health check',
  explanation: 'Explanation',
};

const KIND_LABEL = {
  observation: 'measured',
  inference: 'inferred',
  elimination: 'ruled out',
};

function confidenceTone(value) {
  if (value >= 0.7) return { key: 'strong', word: 'Well supported' };
  if (value >= 0.45) return { key: 'partial', word: 'Partly supported' };
  return { key: 'weak', word: 'Weakly supported' };
}

export default function AnswerPanel({ answer, investigationId, onInvestigate }) {
  if (!answer) return null;

  const tone = confidenceTone(answer.confidence);
  const unresolved = (answer.citations || []).filter((c) => c.status === 'unresolved');
  const unsupported = (answer.reasoning || []).filter(
    (s) => s.kind !== 'observation' && !(s.evidence_ids || []).length,
  );

  return (
    <article className="li-ans animate-fade-in">
      {/* 1 — the answer itself, at a size that says it is the answer. */}
      <header className={`li-ans-verdict li-ans-verdict--${tone.key}`}>
        <div className="li-ans-tags">
          <span className="li-ans-mode">{MODE_LABEL[answer.mode] || answer.mode}</span>
          {answer.root_cause_service && (
            <span className="li-ans-service" title="The component named as the cause">
              {answer.root_cause_service}
            </span>
          )}
        </div>

        <h2 className="li-ans-headline">{answer.headline}</h2>

        {answer.detail && <p className="li-ans-detail">{answer.detail}</p>}

        <Confidence value={answer.confidence} tone={tone} factors={answer.confidence_factors} />
      </header>

      {(unresolved.length > 0 || unsupported.length > 0) && (
        <Caveat unresolved={unresolved} unsupported={unsupported} />
      )}

      {answer.warning_analysis && (
        <Block title="Warnings in this window">
          <p className="li-ans-body">{answer.warning_analysis}</p>
        </Block>
      )}

      {/* 2 — how it got there. */}
      {answer.reasoning?.length > 0 && (
        <Block title="How it reached this"
          hint="Each step, and the evidence it rests on">
          <ol className="li-ans-chain">
            {answer.reasoning.map((step, i) => (
              <ReasoningStep key={i} step={step} index={i + 1} citations={answer.citations} />
            ))}
          </ol>
        </Block>
      )}

      {/* 3 — and what it took for granted to get there. */}
      {answer.assumptions?.length > 0 && (
        <Block title="Assumed, not proven" tone="warn"
          hint="Taken as true without evidence — if one of these is wrong, the answer changes">
          <ul className="li-ans-assumptions">
            {answer.assumptions.map((a, i) => (
              <li key={i} className="li-ans-assumption">
                <p className="li-ans-assumption-text">{a.statement}</p>
                {a.basis && (
                  <p className="li-ans-assumption-line">
                    <span className="li-ans-tag">Why it is reasonable</span>{a.basis}
                  </p>
                )}
                {a.impact_if_wrong && (
                  <p className="li-ans-assumption-line li-ans-assumption-line--risk">
                    <span className="li-ans-tag li-ans-tag--risk">If it is wrong</span>
                    {a.impact_if_wrong}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </Block>
      )}

      {answer.limitations?.length > 0 && (
        <Block title="What this does not establish"
          hint="Questions this investigation cannot answer">
          <ul className="li-ans-limits">
            {answer.limitations.map((l, i) => <li key={i}>{l}</li>)}
          </ul>
        </Block>
      )}

      <NextSteps steps={answer.next_steps} investigationId={investigationId}
        onInvestigate={onInvestigate} />

      {/* Last, and folded away — unless the table *is* the answer.
          A root-cause run can attach thirty rows of supporting records, and
          sitting them between the conclusion and the reasoning pushed the
          working off the screen entirely. For an extraction or an aggregation
          the rows are what was asked for, so those open. */}
      {answer.table && (
        <DataTable table={answer.table}
          open={answer.mode === 'data_extraction' || answer.mode === 'aggregation'} />
      )}
    </article>
  );
}

/** A titled block. Not collapsible: these are the case for the answer. */
function Block({ title, hint, tone, children }) {
  return (
    <section className={`li-ans-block${tone ? ` li-ans-block--${tone}` : ''}`}>
      <h3 className="li-ans-block-title">
        {title}
        {hint && <span className="li-ans-block-hint">{hint}</span>}
      </h3>
      {children}
    </section>
  );
}

/**
 * One link in the chain, on one line where it fits.
 *
 * These were full cards — a circled number, a badge, a 15.5px claim, a separate
 * "because" paragraph and a row of chips, boxed and spaced. Four steps filled a
 * screen, which buried the assumptions underneath them. The chain is the
 * *support* for the answer, not the answer, so it is dense by default: claim and
 * reason run together on one line, the evidence sits inline at the end, and the
 * steps are tied together by a rail rather than by four separate boxes.
 *
 * A step that cites nothing keeps its full weight — that is the one thing here
 * worth interrupting a scan for.
 */
function ReasoningStep({ step, index, citations }) {
  const ids = step.evidence_ids || [];
  const kind = step.kind || 'inference';
  const bare = kind !== 'observation' && ids.length === 0;

  return (
    <li className={`li-ans-step${bare ? ' li-ans-step--bare' : ''}`}>
      <span className={`li-ans-step-dot li-ans-step-dot--${kind}`} aria-hidden="true">
        {index}
      </span>
      <div className="li-ans-step-body">
        <p className="li-ans-step-line">
          <span className={`li-ans-kind li-ans-kind--${kind}`}>{KIND_LABEL[kind] || kind}</span>
          <span className="li-ans-step-claim">{step.claim}</span>
          {step.because && (
            <span className="li-ans-step-because"> — {step.because}</span>
          )}
          <Evidence ids={ids} citations={citations} bare={bare} />
        </p>
      </div>
    </li>
  );
}

function Evidence({ ids, citations, bare }) {
  if (!ids.length) {
    // Only the unsupported case is worth words; "directly observed" on a
    // measurement is a label for something the badge already said.
    return bare
      ? <span className="li-ans-evidence-none">no evidence cited</span>
      : null;
  }
  const byId = Object.fromEntries((citations || []).map((c) => [c.id, c]));
  return (
    <span className="li-ans-evidence">
      {ids.map((id) => {
        const citation = byId[id];
        const bad = citation?.status === 'unresolved';
        return (
          <span key={id} className={`li-ans-cite${bad ? ' li-ans-cite--bad' : ''}`}
            title={bad ? (citation?.detail || 'This id does not resolve to any evidence')
              : (citation?.label || id)}>
            {bad && <span aria-hidden="true">✕ </span>}{id}
          </span>
        );
      })}
    </span>
  );
}

/**
 * The confidence, as a sentence rather than a bare percentage.
 *
 * "78%" answers nothing on its own; "Well supported — 78%" plus the reasons the
 * figure was raised or lowered is something a reader can weigh.
 */
function Confidence({ value, tone, factors }) {
  const [open, setOpen] = useState(false);
  const pct = Math.round((value || 0) * 100);
  const list = factors || [];

  return (
    <div className="li-ans-conf">
      <div className="li-ans-conf-bar" role="img"
        aria-label={`Confidence ${pct} percent, ${tone.word}`}>
        <span className={`li-ans-conf-fill li-ans-conf-fill--${tone.key}`}
          style={{ width: `${pct}%` }} />
      </div>
      <button type="button" className="li-ans-conf-line" onClick={() => setOpen(!open)}
        aria-expanded={open}>
        <strong className={`li-ans-conf-word li-ans-conf-word--${tone.key}`}>{tone.word}</strong>
        <span className="li-ans-conf-pct">{pct}% confidence</span>
        <span className="li-ans-conf-more">{open ? 'Hide why' : 'Why?'}</span>
      </button>
      {open && (
        <ul className="li-ans-factors">
          {list.length === 0 && (
            <li className="li-ans-factor">Nothing adjusted the model's own estimate.</li>
          )}
          {list.map((f, i) => (
            <li key={i} className={`li-ans-factor li-ans-factor--${f.direction}`}>
              <span className="li-ans-factor-dir" aria-hidden="true">
                {f.direction === 'raises' ? '▲' : '▼'}
              </span>
              {f.factor}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Citations that pointed at nothing, and steps that cited nothing at all. */
function Caveat({ unresolved, unsupported }) {
  return (
    <div className="li-ans-caveat">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2.2" aria-hidden="true">
        <path d="M12 9v4M12 17h.01M10.3 3.9L2.7 17a2 2 0 001.7 3h15.2a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"
          strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div>
        <strong>Read this answer with care.</strong>
        <ul>
          {unresolved.length > 0 && (
            <li>
              {unresolved.length} citation{unresolved.length > 1 ? 's' : ''} pointed at evidence
              that does not exist and {unresolved.length > 1 ? 'were' : 'was'} rejected.
            </li>
          )}
          {unsupported.length > 0 && (
            <li>
              {unsupported.length} reasoning step{unsupported.length > 1 ? 's' : ''}{' '}
              {unsupported.length > 1 ? 'cite' : 'cites'} no evidence at all.
            </li>
          )}
        </ul>
      </div>
    </div>
  );
}

function DataTable({ table, open }) {
  if (!table?.rows?.length) return null;
  const shown = table.rows.length;
  return (
    <Collapsible
      title={table.query_description || 'Records found'}
      summary={`${table.total_matched} matched`
        + (table.truncated ? ` · showing the first ${shown}` : '')}
      defaultOpen={Boolean(open)}
    >
      <div className="li-ans-table-wrap">
        <table className="li-ans-table">
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
    </Collapsible>
  );
}
