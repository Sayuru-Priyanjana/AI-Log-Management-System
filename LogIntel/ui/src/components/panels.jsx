import {
  Chip, ConfidenceBar, EmptyNote, RawToggle, ScoreBar, Section, SeverityChip, Stat, Timestamp,
} from './common';

const titleCase = (s) =>
  String(s || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

const signalLabel = (type) => String(type || '').replace(/_/g, ' ');

function magnitudeText(m) {
  if (!m) return null;
  const fmt = (v) => {
    if (v === null || v === undefined) return 'n/a';
    const n = Number(v);
    if (Math.abs(n) >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
    if (Math.abs(n) >= 1) return n.toFixed(2);
    return String(n.toFixed(4)).replace(/0+$/, '').replace(/\.$/, '');
  };
  let text = `${fmt(m.incident)} ${m.unit || ''}`.trim();
  if (m.baseline !== null && m.baseline !== undefined) text += ` (baseline ${fmt(m.baseline)} ${m.unit || ''})`.replace('  ', ' ');
  if (m.ratio !== null && m.ratio !== undefined) text += ` — ${m.ratio.toFixed(1)}x`;
  return text;
}

// ---------------------------------------------------------------- Plan -----
export function PlanPanel({ data }) {
  if (!data) return <EmptyNote>No plan yet.</EmptyNote>;
  return (
    <div className="li-panel">
      <Section title="Investigation goal">
        <p className="li-goal">{data.goal}</p>
        <div className="li-chiprow">
          <Chip tone="accent">{titleCase(data.intent)}</Chip>
          <Chip tone={data.planner === 'llm' ? 'good' : 'neutral'}>
            planned by {data.planner === 'llm' ? 'the model' : 'heuristics (model unavailable)'}
          </Chip>
          <Chip>{data.environment}</Chip>
        </div>
      </Section>

      <Section title="Scope">
        <div className="li-kv">
          <div><span>System</span><strong>{data.system_name} ({data.system_id})</strong></div>
          <div>
            <span>Focus service</span>
            <strong>{data.service || 'whole system (no single service named)'}</strong>
          </div>
          <div><span>Namespaces</span><strong>{(data.namespaces || []).join(', ') || '—'}</strong></div>
          <div>
            <span>Requested window</span>
            <strong><Timestamp value={data.requested_window?.start} /> → <Timestamp value={data.requested_window?.end} /></strong>
          </div>
        </div>
      </Section>

      <Section title="Evidence tools to run">
        <div className="li-chiprow">
          {(data.tools || []).map((t) => <Chip key={t}>{t}</Chip>)}
        </div>
      </Section>

      {data.notes?.length > 0 && (
        <Section title="Planner notes" badge={<Chip tone="bad">transparency</Chip>}>
          <ul className="li-notelist">
            {data.notes.map((note, i) => <li key={i}>{note}</li>)}
          </ul>
        </Section>
      )}

      <RawToggle data={data} />
    </div>
  );
}

// -------------------------------------------------------------- Windows ----
function WindowBar({ label, window, tone }) {
  if (!window) return null;
  const start = new Date(window.start).getTime();
  const end = new Date(window.end).getTime();
  const minutes = Math.max(0, (end - start) / 60000);
  return (
    <div className="li-windowbar">
      <div className="li-windowbar-label">
        <span>{label}</span>
        <span className="li-mono">{minutes.toFixed(0)}m</span>
      </div>
      <div className={`li-windowbar-track li-windowbar-track--${tone}`} />
      <div className="li-windowbar-range">
        <Timestamp value={window.start} /> <span>→</span> <Timestamp value={window.end} />
      </div>
    </div>
  );
}

export function WindowsPanel({ data }) {
  if (!data) return <EmptyNote>No windows yet.</EmptyNote>;
  return (
    <div className="li-panel">
      <Section title="Onset detection"
        badge={<Chip tone={data.onset_detected ? 'accent' : 'neutral'}>
          {data.onset_detected ? 'onset found' : 'no onset found'}
        </Chip>}>
        <p className="li-body-text">{data.method}</p>
        {data.onset && (
          <p className="li-body-text">Departure from baseline: <Timestamp value={data.onset} /></p>
        )}
        {data.onset_before_window && (
          <Chip tone="bad">errors were already elevated at the edge of the search range</Chip>
        )}
        <p className="li-muted">Scanned {data.search_buckets} one-minute buckets to find it.</p>
      </Section>

      <Section title="Windows compared">
        <WindowBar label="Incident window (what was analyzed)" window={data.incident} tone="incident" />
        {data.baseline
          ? <WindowBar label="Baseline window (what 'normal' looks like)" window={data.baseline} tone="baseline" />
          : <Chip tone="bad">no baseline window available — confidence will be capped</Chip>}
      </Section>

      <RawToggle data={data} />
    </div>
  );
}

// -------------------------------------------------------------- Evidence ---
function StatusChip({ status }) {
  const tone = status === 'ok' ? 'good' : status === 'partial' ? 'neutral' : 'bad';
  return <Chip tone={tone}>{status}</Chip>;
}

export function EvidencePanel({ data }) {
  if (!data) return <EmptyNote>No evidence yet.</EmptyNote>;
  const logs = data.logs || {};
  const events = data.events || {};
  const metrics = data.metrics || {};
  return (
    <div className="li-panel">
      <Section title="Source status">
        <div className="li-chiprow">
          <span>Logs <StatusChip status={data.statuses?.logs} /></span>
          <span>Events <StatusChip status={data.statuses?.events} /></span>
          <span>Metrics <StatusChip status={data.statuses?.metrics} /></span>
        </div>
      </Section>

      {data.gaps?.length > 0 && (
        <Section title="Gaps" badge={<Chip tone="bad">confidence will be capped</Chip>}>
          <ul className="li-notelist">
            {data.gaps.map((gap, i) => <li key={i}>{gap}</li>)}
          </ul>
        </Section>
      )}

      <Section title="Application logs">
        <div className="li-statrow">
          <Stat label="documents (incident)" value={logs.documents ?? 0} />
          <Stat label="documents (baseline)" value={logs.baseline_documents ?? 0} />
          <Stat label="patterns found" value={logs.patterns ?? 0} />
          <Stat label="new error patterns" value={logs.new_error_patterns ?? 0}
            tone={logs.new_error_patterns > 0 ? 'warn' : undefined} />
          <Stat label="unparsed lines" value={logs.unparsed ?? 0}
            tone={logs.unparsed > 0 ? 'warn' : undefined} />
        </div>
        {logs.by_level && (
          <div className="li-chiprow" style={{ marginTop: 10 }}>
            {Object.entries(logs.by_level).map(([level, count]) => (
              <Chip key={level}>{level}: {count}</Chip>
            ))}
          </div>
        )}
      </Section>

      <Section title="Kubernetes events">
        <div className="li-statrow">
          <Stat label="events in scope" value={events.count ?? 0} />
          <Stat label="warnings/critical" value={events.warnings ?? 0}
            tone={events.warnings > 0 ? 'warn' : undefined} />
        </div>
      </Section>

      <Section title="Metrics">
        <div className="li-statrow">
          <Stat label="series collected" value={metrics.series ?? 0} />
          <Stat label="unavailable" value={metrics.unavailable?.length ?? 0}
            tone={metrics.unavailable?.length ? 'warn' : undefined} />
        </div>
        {metrics.unavailable?.length > 0 && (
          <div className="li-chiprow" style={{ marginTop: 10 }}>
            {metrics.unavailable.map((m) => <Chip key={m} tone="bad">{m}</Chip>)}
          </div>
        )}
      </Section>

      <RawToggle data={data} />
    </div>
  );
}

// -------------------------------------------------------------- Signals ----
function SignalCard({ signal }) {
  const magnitude = magnitudeText(signal.magnitude);
  return (
    <div className="li-card">
      <div className="li-card-head">
        <SeverityChip level={signal.severity} />
        <strong className="li-card-title">{signalLabel(signal.type)}</strong>
        <span className="li-spacer" />
        {signal.first_seen && <Timestamp value={signal.first_seen} />}
      </div>
      <div className="li-chiprow" style={{ marginTop: 6 }}>
        {signal.service && <Chip tone="accent">service: {signal.service}</Chip>}
        {signal.pod && <Chip>pod: {signal.pod}</Chip>}
        {magnitude && <Chip>{magnitude}</Chip>}
      </div>
      <p className="li-body-text" style={{ marginTop: 8 }}>{signal.description}</p>
      {signal.evidence_ids?.length > 0 && (
        <div className="li-chiprow" style={{ marginTop: 6 }}>
          {signal.evidence_ids.map((id) => <span key={id} className="li-mono li-idchip">{id}</span>)}
        </div>
      )}
    </div>
  );
}

export function SignalsPanel({ data }) {
  if (!data) return <EmptyNote>No signals yet.</EmptyNote>;
  const signals = data.signals || [];
  return (
    <div className="li-panel">
      <p className="li-muted">
        {signals.length === 0
          ? 'Nothing crossed its threshold. Every check is baseline-relative, so this is a real "no change detected", not a lack of data.'
          : `${signals.length} signal(s), ordered by when each one actually started.`}
      </p>
      {signals.map((s) => <SignalCard key={s.id} signal={s} />)}
      <RawToggle data={data} />
    </div>
  );
}

// ------------------------------------------------------------ Candidates ---
function CandidateCard({ candidate, rank, chosenId, engineTopId }) {
  const isChosen = candidate.id === chosenId;
  const isEngineTop = candidate.id === engineTopId;
  return (
    <div className={`li-card ${isChosen ? 'li-card--chosen' : ''}`}>
      <div className="li-card-head">
        <span className="li-rank">#{rank}</span>
        <strong className="li-card-title">{candidate.hypothesis}</strong>
        <span className="li-spacer" />
        {isChosen && <Chip tone="good">chosen</Chip>}
        {isEngineTop && !isChosen && <Chip tone="accent">rules ranked this #1</Chip>}
      </div>
      <div className="li-chiprow" style={{ margin: '8px 0' }}>
        <Chip>{titleCase(candidate.category)}</Chip>
        {candidate.service && <Chip tone="accent">{candidate.service}</Chip>}
        {candidate.onset && <span className="li-mono"><Timestamp value={candidate.onset} /></span>}
      </div>
      <ScoreBar value={candidate.score} />
      <p className="li-body-text" style={{ marginTop: 8 }}>{candidate.rationale}</p>
      {candidate.supporting_signals?.length > 0 && (
        <div className="li-chiprow" style={{ marginTop: 6 }}>
          <span className="li-muted" style={{ fontSize: 12 }}>supports:</span>
          {candidate.supporting_signals.map((id) => (
            <span key={id} className="li-mono li-idchip li-idchip--good">{id}</span>
          ))}
        </div>
      )}
      {candidate.contradicting_signals?.length > 0 && (
        <div className="li-chiprow" style={{ marginTop: 6 }}>
          <span className="li-muted" style={{ fontSize: 12 }}>argues against:</span>
          {candidate.contradicting_signals.map((id) => (
            <span key={id} className="li-mono li-idchip li-idchip--bad">{id}</span>
          ))}
        </div>
      )}
    </div>
  );
}

export function CandidatesPanel({ data, chosenId, engineTopId }) {
  if (!data) return <EmptyNote>No candidates yet.</EmptyNote>;
  const candidates = data.candidates || [];
  return (
    <div className="li-panel">
      <p className="li-muted">
        Generated deterministically by rules over the signals above — the model chooses one of
        these next, it does not author its own.
      </p>
      {candidates.map((c, i) => (
        <CandidateCard key={c.id} candidate={c} rank={i + 1} chosenId={chosenId} engineTopId={engineTopId} />
      ))}
      <RawToggle data={data} />
    </div>
  );
}

// -------------------------------------------------------------- Analysis ---
export function AnalysisPanel({ data }) {
  if (!data) return <EmptyNote>No analysis yet.</EmptyNote>;
  return (
    <div className="li-panel">
      <Section title="Chosen cause">
        <p className="li-cause">{data.cause}</p>
      </Section>
      <ConfidenceBar value={data.confidence} />
      <Section title="Explanation">
        <p className="li-body-text">{data.summary}</p>
      </Section>
      <RawToggle data={data} />
    </div>
  );
}

// -------------------------------------------------------------- Verified ---
export function VerifiedPanel({ data }) {
  if (!data) return <EmptyNote>No verification yet.</EmptyNote>;
  const message = data.message || '';
  const lines = message.split('\n');
  const hasIssues = lines.some((l) => l.trim().startsWith('- '));
  return (
    <div className="li-panel">
      <Section title={hasIssues ? 'Verified with notes' : 'Verified clean'}
        badge={<Chip tone={hasIssues ? 'neutral' : 'good'}>{hasIssues ? 'see notes' : 'no contradictions'}</Chip>}>
        {hasIssues ? (
          <ul className="li-notelist">
            {lines.filter((l) => l.trim().startsWith('- ')).map((l, i) => (
              <li key={i}>{l.trim().slice(2)}</li>
            ))}
          </ul>
        ) : (
          <p className="li-body-text">{message}</p>
        )}
      </Section>
      <RawToggle data={data} />
    </div>
  );
}

// ---------------------------------------------------------------- Report ---
export function ReportPanel({ data }) {
  if (!data) return <EmptyNote>No report yet.</EmptyNote>;
  const analysis = data.analysis || {};
  return (
    <div className="li-panel">
      <Section title="Verdict" badge={
        <Chip tone={analysis.incident_detected ? 'bad' : 'good'}>
          {analysis.incident_detected ? 'incident detected' : 'no incident'}
        </Chip>
      }>
        <div className="li-chiprow">
          <SeverityChip level={analysis.severity} />
          <Chip>{titleCase(analysis.category)}</Chip>
          <Chip tone={analysis.analyst === 'llm' ? 'accent' : 'neutral'}>
            analyst: {analysis.analyst === 'llm' ? 'model (verified)' : 'deterministic fallback'}
          </Chip>
          {analysis.agrees_with_engine === false && (
            <Chip tone="bad">disagreed with the rule engine's top pick</Chip>
          )}
        </div>
        <p className="li-cause" style={{ marginTop: 12 }}>{analysis.cause_summary}</p>
        <ConfidenceBar value={analysis.confidence} />
      </Section>

      {analysis.narrative && (
        <Section title="Narrative">
          <p className="li-body-text">{analysis.narrative}</p>
        </Section>
      )}

      {analysis.timeline?.length > 0 && (
        <Section title="Timeline">
          <ol className="li-timeline">
            {analysis.timeline.map((entry, i) => <li key={i}>{entry}</li>)}
          </ol>
        </Section>
      )}

      {analysis.next_steps?.length > 0 && (
        <Section title="Recommended next steps">
          <ul className="li-checklist">
            {analysis.next_steps.map((step, i) => <li key={i}>{step}</li>)}
          </ul>
        </Section>
      )}

      {analysis.evidence_gaps?.length > 0 && (
        <Section title="Evidence gaps" badge={<Chip tone="bad">absence here ≠ health</Chip>}>
          <ul className="li-notelist">
            {analysis.evidence_gaps.map((gap, i) => <li key={i}>{gap}</li>)}
          </ul>
        </Section>
      )}

      {analysis.verification?.length > 0 && (
        <Section title="Verification log" badge={<Chip tone="accent">every check the verifier ran</Chip>}>
          <div className="li-verification">
            {analysis.verification.map((issue, i) => (
              <div key={i} className={`li-verification-row li-verification-row--${issue.severity}`}>
                <Chip tone={issue.severity === 'error' ? 'bad' : 'neutral'}>{issue.code}</Chip>
                <span>{issue.detail}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {analysis.evidence_ids?.length > 0 && (
        <Section title="Cited evidence">
          <div className="li-chiprow">
            {analysis.evidence_ids.map((id) => (
              <span key={id} className="li-mono li-idchip">{id}</span>
            ))}
          </div>
        </Section>
      )}

      <Section title="Run details">
        <div className="li-kv">
          <div><span>Investigation ID</span><strong className="li-mono">{data.id}</strong></div>
          <div><span>Persisted</span><strong>{data.persisted ? 'yes — saved to OpenSearch' : 'no'}</strong></div>
        </div>
        {data.timings_ms && (
          <div className="li-chiprow" style={{ marginTop: 10 }}>
            {Object.entries(data.timings_ms).map(([stage, ms]) => (
              <Chip key={stage}>{stage}: {ms}ms</Chip>
            ))}
          </div>
        )}
        {data.errors?.length > 0 && (
          <ul className="li-notelist" style={{ marginTop: 10 }}>
            {data.errors.map((err, i) => <li key={i}>{err}</li>)}
          </ul>
        )}
      </Section>

      <RawToggle data={data} label="View full raw InvestigationResult" />
    </div>
  );
}
