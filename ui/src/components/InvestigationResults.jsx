import { useInvestigation } from '../InvestigationContext';
import { usePreferences } from '../preferences';
import AnswerPanel from './AnswerPanel';
import EvidenceTimeline from './EvidenceTimeline';
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

export default function InvestigationResults({ onFollowUp }) {
  const {
    request, stages, trace, answer, evidenceTimeline, result,
    status, elapsed, errorDetail, stopInvestigation
  } = useInvestigation();

  // If there's an error from context that wasn't displayed, we could show it,
  // but it's handled in the context. We'll just show the error detail in the UI.

  const seconds = (elapsed / 1000).toFixed(1);
  const plan = stages.plan;

  const parseQuestion = (q) => {
    if (!q) return { goal: '', payload: null, rawPayload: null };
    const marker = 'Detection payload:\n';
    const idx = q.indexOf(marker);
    if (idx === -1) return { goal: q, payload: null, rawPayload: null };
    
    const goal = q.substring(0, idx).trim();
    const jsonStr = q.substring(idx + marker.length).trim();
    let payload = null;
    try {
      payload = JSON.parse(jsonStr);
    } catch (e) {
      // ignore
    }
    return { goal, payload, rawPayload: jsonStr };
  };

  const { goal, payload, rawPayload } = parseQuestion(request.question);

  return (
    <div className="li-results">
      <div className="li-results-header">
        <span className="li-goal">{goal}</span>
        {plan?.service && <span className="li-chip li-chip--service">{plan.service}</span>}
        <span className="spacer" />
        <div className="li-status-pill" style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {status === 'connecting' && <StatusDot color="var(--warning)" label="Connecting…" pulse />}
          {status === 'streaming' && <StatusDot color="var(--accent-color)" label={`Working… ${seconds}s`} pulse />}
          {status === 'complete' && <StatusDot color="var(--success)" label={`Done in ${seconds}s`} />}
          {status === 'error' && <StatusDot color="var(--error)" label="Failed" />}
          
          {(status === 'streaming' || status === 'connecting') && (
            <button 
              type="button" 
              onClick={stopInvestigation}
              style={{
                background: 'var(--error-bg)', color: 'var(--error)',
                border: '1px solid var(--error)', padding: '2px 8px',
                borderRadius: '4px', fontSize: '12px', cursor: 'pointer',
                fontWeight: 500
              }}
            >
              Stop
            </button>
          )}
        </div>
      </div>
      
      {payload ? (
        <DetectionPayloadCard payload={payload} />
      ) : rawPayload ? (
        <div className="glass-panel" style={{ marginTop: '16px', marginBottom: '24px' }}>
          <pre style={{ margin: 0, overflowX: 'auto', fontSize: '12px', fontFamily: 'var(--mono)' }}>{rawPayload}</pre>
        </div>
      ) : null}

      {errorDetail && (
        <div className="glass-panel" style={{ color: 'var(--error)', borderLeft: '3px solid var(--error)' }}>
          <p style={{ margin: 0 }}><strong>Error:</strong> {errorDetail}</p>
        </div>
      )}


      <WindowBanner windows={stages.windows} />

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

      <EvidenceTimeline data={evidenceTimeline} />

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

function DetectionPayloadCard({ payload }) {
  const SEVERITY_TONE = { high: 'var(--error)', medium: 'var(--warning)', low: 'var(--text-2)' };
  const tone = SEVERITY_TONE[payload.severity] || 'var(--accent-color)';
  
  return (
    <div className="glass-panel" style={{ 
      marginTop: '16px', 
      marginBottom: '16px',
      borderLeft: `3px solid ${tone}`,
      display: 'flex',
      flexDirection: 'column',
      gap: '12px'
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <span style={{ 
          background: `color-mix(in srgb, ${tone} 15%, transparent)`, 
          color: tone, 
          padding: '4px 8px', 
          borderRadius: '4px', 
          fontWeight: 600, 
          fontSize: '11px', 
          textTransform: 'uppercase',
          letterSpacing: '0.5px'
        }}>
          Alert Payload
        </span>
        <span style={{ fontFamily: 'var(--mono)', fontSize: '13px', color: 'var(--text-1)', fontWeight: 500 }}>
          {payload.rule || payload.title || 'Detection'}
        </span>
        <span className="spacer" />
        <span style={{ fontSize: '12px', color: 'var(--text-3)' }}>
          {payload.detected_at || payload.timestamp}
        </span>
      </div>

      {payload.summary && (
        <div style={{ fontSize: '14px', lineHeight: 1.5, color: 'var(--text-1)' }}>
          {payload.summary}
        </div>
      )}

      <div style={{ 
        display: 'flex', 
        flexWrap: 'wrap', 
        gap: '24px', 
        marginTop: '4px',
        paddingTop: '12px',
        borderTop: '1px solid var(--border-color)'
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <span style={{ fontSize: '11px', color: 'var(--text-3)', textTransform: 'uppercase', fontWeight: 600 }}>System</span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: '13px' }}>{payload.system_id || '—'}</span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <span style={{ fontSize: '11px', color: 'var(--text-3)', textTransform: 'uppercase', fontWeight: 600 }}>Service</span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: '13px' }}>{payload.service || '—'}</span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <span style={{ fontSize: '11px', color: 'var(--text-3)', textTransform: 'uppercase', fontWeight: 600 }}>Severity</span>
          <span style={{ fontSize: '13px', color: tone, fontWeight: 500 }}>{payload.severity || '—'}</span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <span style={{ fontSize: '11px', color: 'var(--text-3)', textTransform: 'uppercase', fontWeight: 600 }}>Window</span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: '13px' }}>{payload.window || '—'}</span>
        </div>
      </div>
    </div>
  );
}

/**
 * The period actually examined, and what it was compared against.
 *
 * Every signal is a ratio against the baseline, so which stretch was used is not
 * a footnote — it decides what could be detected at all. A degraded or missing
 * baseline is stated plainly rather than left for the reader to infer from a
 * confidence factor.
 */
function WindowBanner({ windows }) {
  const { formatClock, formatDay, zoneLabel } = usePreferences();
  if (!windows) return null;

  const quality = windows.baseline_quality || (windows.baseline ? 'clean' : 'none');
  const tone = { clean: '', degraded: 'li-window--degraded', none: 'li-window--none' }[quality];

  return (
    <div className={`glass-panel li-window ${tone}`}>
      <div className="li-window-part">
        <span className="li-window-label">Analysed</span>
        <span className="li-window-value">
          {formatClock(windows.incident?.start)} – {formatClock(windows.incident?.end)}
        </span>
        <span className="li-window-sub">{span(windows.incident)} · {formatDay(windows.incident?.start)} · {zoneLabel}</span>
      </div>

      <div className="li-window-part">
        <span className="li-window-label">Compared against</span>
        {windows.baseline ? (
          <>
            <span className="li-window-value">
              {formatClock(windows.baseline.start)} – {formatClock(windows.baseline.end)}
            </span>
            <span className="li-window-sub">
              {span(windows.baseline)}
              {quality === 'degraded' && ' · may itself have been unhealthy'}
            </span>
          </>
        ) : (
          <>
            <span className="li-window-value li-window-value--warn">nothing</span>
            <span className="li-window-sub">
              most detection is baseline-relative and could not run
            </span>
          </>
        )}
      </div>

      <div className="li-window-part li-window-part--wide">
        <span className="li-window-label">
          {windows.onset_detected ? 'Onset' : 'No onset detected'}
        </span>
        {windows.onset && (
          <span className="li-window-value">{formatClock(windows.onset)}</span>
        )}
        <span className="li-window-sub">{windows.method}</span>
      </div>
    </div>
  );
}

function span(window) {
  if (!window?.start || !window?.end) return '';
  const minutes = (new Date(window.end) - new Date(window.start)) / 60000;
  if (minutes < 90) return `${Math.round(minutes)} min`;
  return `${(minutes / 60).toFixed(1)} h`;
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
