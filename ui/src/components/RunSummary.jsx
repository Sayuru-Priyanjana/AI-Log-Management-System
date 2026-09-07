import { usePreferences } from '../preferences';

/**
 * The run, in one line above the answer.
 *
 * The window banner, the workflow graph, the stage strip and the token usage
 * were four full-width panels stacked in front of the answer — roughly a
 * screen of scaffolding to say "an hour was analysed, eight nodes ran, four
 * signals were found, four model calls were made". Every one of those facts is
 * worth having and none of them is worth a panel.
 *
 * They are one dense row here, and the panels themselves still exist under
 * "Run details" for when a figure needs checking rather than glancing at.
 */
export default function RunSummary({ stages, llmUsage, graph, result }) {
  const { formatClock, zoneLabel } = usePreferences();
  const windows = stages?.windows;
  const signals = stages?.signals;
  const candidates = stages?.candidates?.candidates || [];

  const facts = [];

  // What the agent recalled of this conversation on its own.
  //
  // The LangGraph backend keeps the thread in a `memory` channel restored by
  // its checkpointer, and reports how many turns it had when it planned this
  // one. Without showing it, "does it remember?" was answerable only by reading
  // the answer and guessing — and the client's own history looks identical from
  // the outside whether or not the agent used it.
  const recalled = stages?.plan?.remembered_turns;
  if (typeof recalled === 'number') {
    facts.push({
      label: 'Memory',
      value: recalled === 0 ? 'first question' : `${recalled} turn${recalled === 1 ? '' : 's'}`,
      note: recalled === 0
        ? 'nothing earlier in this thread'
        : 'recalled by the agent (LangGraph)',
    });
  }

  if (windows?.incident?.start && windows?.incident?.end) {
    facts.push({
      label: 'Analysed',
      value: `${formatClock(windows.incident.start)}–${formatClock(windows.incident.end)}`,
      note: span(windows.incident),
      title: `Incident window, shown in ${zoneLabel}. Compared against `
        + (windows.baseline
          ? `${formatClock(windows.baseline.start)}–${formatClock(windows.baseline.end)}.`
          : 'nothing — no clean baseline could be found.'),
      // A missing baseline silently disables most of the detection, so it is
      // never left to be inferred from a quieter confidence number.
      tone: windows.baseline ? undefined : 'warn',
    });
  }

  if (windows) {
    facts.push(windows.onset_detected
      ? { label: 'Onset', value: formatClock(windows.onset), note: windows.method }
      : { label: 'Onset', value: 'not found', note: windows.method, tone: 'warn' });
  }

  if (signals) {
    facts.push({
      label: 'Signals',
      value: String(signals.count ?? 0),
      note: candidates[0]?.category ? `ranks ${candidates[0].category.replace(/_/g, ' ')}` : 'none ranked',
      tone: signals.count ? 'alert' : undefined,
    });
  }

  if (stages?.evidence?.logs) {
    facts.push({
      label: 'Evidence',
      value: compact(stages.evidence.logs.documents),
      note: 'log documents',
    });
  }

  if (llmUsage) {
    const pct = llmUsage.peak_context_used == null
      ? null : Math.round(llmUsage.peak_context_used * 100);
    facts.push({
      label: 'Model',
      value: llmUsage.model || 'unknown',
      note: `${llmUsage.requests} call${llmUsage.requests === 1 ? '' : 's'}`
        + ` · ${compact(llmUsage.peak_prompt_tokens)} peak prompt`
        + (pct == null ? '' : ` (${pct}% of window)`),
      mono: true,
      tone: pct != null && pct >= 90 ? 'warn' : undefined,
    });
  }

  const nodes = graph?.nodes?.length || result?.graph_path?.length;
  if (nodes) {
    facts.push({
      label: 'Workflow',
      value: graph?.engine === 'langgraph' ? 'LangGraph' : 'Pipeline',
      note: `${result?.graph_path?.length || nodes} nodes ran`,
    });
  }

  if (!facts.length) return null;

  return (
    <div className="li-run">
      {facts.map((f) => (
        <div key={f.label} className={`li-run-fact${f.tone ? ` li-run-fact--${f.tone}` : ''}`}
          title={f.title}>
          <span className="li-run-label">{f.label}</span>
          <span className={`li-run-value${f.mono ? ' li-run-value--mono' : ''}`}>{f.value}</span>
          {f.note && <span className="li-run-note">{f.note}</span>}
        </div>
      ))}
    </div>
  );
}

function span(window) {
  if (!window?.start || !window?.end) return '';
  const minutes = (new Date(window.end) - new Date(window.start)) / 60000;
  if (minutes < 90) return `${Math.round(minutes)} min`;
  if (minutes < 2880) return `${(minutes / 60).toFixed(1)} h`;
  return `${(minutes / 1440).toFixed(1)} d`;
}

function compact(n) {
  if (!n && n !== 0) return '—';
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}
