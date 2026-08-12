import { useEffect, useRef, useState } from 'react';
import { runInvestigation } from '../api';
import AnswerPanel from './AnswerPanel';
import { ErrorBanner } from './common';
import ReasoningTrace from './ReasoningTrace';

// The stages that run before the model does. Shown as a compact strip rather
// than a full-width diagram: they are fast, they always succeed or fail
// together, and the interesting part of the run is what comes after them.
const PREP_STAGES = [
  { id: 'plan', label: 'Plan' },
  { id: 'windows', label: 'Window' },
  { id: 'evidence', label: 'Evidence' },
  { id: 'signals', label: 'Signals' },
  { id: 'candidates', label: 'Candidates' },
];

export default function InvestigationResults({ request, onFollowUp }) {
  const [stages, setStages] = useState({});
  const [trace, setTrace] = useState([]);
  const [answer, setAnswer] = useState(null);
  const [result, setResult] = useState(null);
  const [status, setStatus] = useState('connecting');
  const [errorMsg, setErrorMsg] = useState(null);
  const startedAt = useRef(Date.now());
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let mounted = true;
    startedAt.current = Date.now();
    setStages({}); setTrace([]); setAnswer(null); setResult(null); setErrorMsg(null);

    const tick = setInterval(() => {
      if (mounted) {
        setStatus((s) => {
          if (s !== 'complete' && s !== 'error') setElapsed(Date.now() - startedAt.current);
          return s;
        });
      }
    }, 250);

    (async () => {
      try {
        setStatus('connecting');
        await runInvestigation(request, {
          signal: controller.signal,
          onEvent: (event) => {
            if (!mounted) return;
            const { stage, data } = event;

            if (stage === 'error') {
              setStatus('error');
              setErrorMsg(data?.detail || 'The investigation failed.');
              return;
            }
            setStatus('streaming');

            if (stage === 'reasoning') {
              setTrace((prev) => [...prev, data]);
            } else if (stage === 'answer') {
              setAnswer(data);
            } else if (stage === 'result') {
              setResult(data);
            } else {
              setStages((prev) => ({ ...prev, [stage]: data }));
            }
          },
        });
        if (mounted) setStatus((s) => (s === 'error' ? s : 'complete'));
      } catch (err) {
        if (mounted && err.name !== 'AbortError') {
          setStatus('error');
          setErrorMsg(err.message);
        }
      }
    })();

    return () => {
      mounted = false;
      clearInterval(tick);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request]);

  const seconds = (elapsed / 1000).toFixed(1);
  const plan = stages.plan;

  return (
    <div className="li-results">
      <div className="glass-panel li-results-header">
        <div style={{ minWidth: 0 }}>
          <h3>
            <span className="text-gradient">{request.system_id}</span>
            {plan?.service ? <span className="li-muted"> · {plan.service}</span> : null}
          </h3>
          <p className="li-muted">{request.question}</p>
        </div>
        <div className="li-status-pill">
          {status === 'connecting' && <StatusDot color="var(--warning)" label="Connecting…" pulse />}
          {status === 'streaming' && <StatusDot color="var(--accent-color)" label={`Working… ${seconds}s`} pulse />}
          {status === 'complete' && <StatusDot color="var(--success)" label={`Done in ${seconds}s`} />}
          {status === 'error' && <StatusDot color="var(--error)" label="Failed" />}
        </div>
      </div>

      {errorMsg && <ErrorBanner><strong>Something went wrong.</strong> {errorMsg}</ErrorBanner>}

      <PrepStrip stages={stages} />

      {/* The answer sits above the trace once it exists: the conclusion is what
          most readers want, and the working is there for when they doubt it. */}
      {answer && (
        <div className="glass-panel li-answer-panel">
          {/* The id only exists once the run is stored, so tool buttons appear
              when they can actually work rather than failing when pressed. */}
          <AnswerPanel answer={answer} investigationId={result?.id}
            onInvestigate={onFollowUp} />
        </div>
      )}

      <ReasoningTrace steps={trace} live={status === 'streaming' && !answer} />

      {!answer && status === 'streaming' && trace.length === 0 && (
        <div className="glass-panel li-waiting">
          <p>Gathering evidence…</p>
        </div>
      )}

      {result && <RunFooter result={result} />}
    </div>
  );
}

function PrepStrip({ stages }) {
  const signals = stages.signals?.signals || [];
  const candidates = stages.candidates?.candidates || [];

  return (
    <div className="glass-panel li-prep">
      {PREP_STAGES.map((stage) => {
        const done = Boolean(stages[stage.id]);
        return (
          <div key={stage.id} className={`li-prep-node ${done ? 'li-prep-node--done' : ''}`}
            title={summarise(stage.id, stages[stage.id])}>
            <span className="li-prep-dot" />
            <span>{stage.label}</span>
            {done && <span className="li-prep-value">{shortValue(stage.id, stages[stage.id])}</span>}
          </div>
        );
      })}
      {signals.length > 0 && (
        <div className="li-prep-signals">
          {signals.slice(0, 6).map((s) => (
            <span key={s.id} className={`li-chip li-chip--${s.severity}`}
              title={`${s.description}${s.magnitude ? ` — ${s.magnitude.incident} ${s.magnitude.unit}` : ''}`}>
              {s.type}
            </span>
          ))}
          {signals.length > 6 && <span className="li-muted">+{signals.length - 6} more</span>}
        </div>
      )}
      {signals.length === 0 && stages.signals && (
        <span className="li-muted" style={{ paddingLeft: 8 }}>
          nothing crossed a threshold
        </span>
      )}
      {candidates.length > 0 && (
        <span className="li-muted" style={{ paddingLeft: 8 }}>
          rules rank: <strong>{candidates[0].category}</strong>
          {candidates[0].service ? ` (${candidates[0].service})` : ''}
        </span>
      )}
    </div>
  );
}

function shortValue(id, data) {
  if (!data) return '';
  if (id === 'evidence') return `${data.logs?.documents ?? 0} logs`;
  if (id === 'signals') return `${data.count ?? 0}`;
  if (id === 'candidates') return `${(data.candidates || []).length}`;
  if (id === 'windows') return data.onset_detected ? 'onset found' : 'no onset';
  if (id === 'plan') return data.answer_mode?.replace('_', ' ') || '';
  return '';
}

function summarise(id, data) {
  if (!data) return 'pending';
  if (id === 'windows') return `${data.method || ''}`;
  if (id === 'evidence') return (data.gaps || []).join('; ') || 'no evidence gaps';
  if (id === 'plan') return data.notes?.join('; ') || `intent: ${data.intent}`;
  return '';
}

function RunFooter({ result }) {
  const timings = result.timings_ms || {};
  const total = Object.values(timings).reduce((a, b) => a + b, 0) / 1000;
  return (
    <div className="li-run-footer li-muted">
      <span>{result.id}</span>
      <span>·</span>
      <span>{total.toFixed(1)}s</span>
      <span>·</span>
      <span>analyst: {result.analysis?.analyst}</span>
      {result.errors?.length > 0 && (
        <>
          <span>·</span>
          <span className="text-warning">{result.errors.length} warning(s)</span>
        </>
      )}
    </div>
  );
}

function StatusDot({ color, label, pulse }) {
  return (
    <>
      <div className={pulse ? 'animate-pulse-glow' : ''}
        style={{ width: 9, height: 9, borderRadius: '50%', background: color, flexShrink: 0 }} />
      <span style={{ color, fontWeight: 550 }}>{label}</span>
    </>
  );
}
