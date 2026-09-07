import { useEffect, useRef, useState } from 'react';
import { useInvestigation } from '../InvestigationContext';
import { usePreferences } from '../preferences';
import InvestigationResults from './InvestigationResults';

/**
 * The whole chat, rather than only its most recent question.
 *
 * A follow-up used to replace the page: the earlier turns survived in state but
 * were rendered as a bare `<details>` holding two paragraphs, so asking a
 * second question destroyed the evidence, the reasoning trace and the graph
 * that justified the first. That made the thread unreadable in the way that
 * matters — you could no longer check an answer against the working that
 * produced it, or see what changed between two questions about the same
 * incident.
 *
 * Here every turn is the same component the live run uses, so an earlier
 * question keeps its full analysis. Earlier turns start collapsed to their
 * conclusion, because a thread of four complete investigations is a lot of page
 * and the conclusion is what you scan for; one click restores the detail.
 */
export default function ConversationThread({ onFollowUp, onAsk }) {
  return (
    <div className="li-conversation">
      <Thread onFollowUp={onFollowUp} onAsk={onAsk} />
    </div>
  );
}

function Thread({ onFollowUp, onAsk }) {
  const { chatHistory, request, status, result, answer } = useInvestigation();
  const turns = chatHistory || [];
  const liveIndex = turns.length;

  // Bring a newly started follow-up into view. Without this the page keeps the
  // scroll position of the turn above and a question appears to do nothing.
  const liveRef = useRef(null);
  const startedFor = useRef(null);
  useEffect(() => {
    if (!request || startedFor.current === request) return;
    startedFor.current = request;
    if (liveIndex > 0) {
      liveRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [request, liveIndex]);

  return (
    <>
      {/* The thread scrolls; the composer does not. Asking the next question
          should never require scrolling to the end of four investigations to
          find the box — it is the primary action of the page, so it is pinned
          to the bottom of the pane and always on screen. */}
      <div className="li-thread">
        {turns.map((turn, i) => (
          <TurnCard key={i} index={i} total={liveIndex + 1} turn={turn}
            onFollowUp={onFollowUp} />
        ))}

        <div ref={liveRef}>
          <TurnCard index={liveIndex} total={liveIndex + 1} live
            onFollowUp={onFollowUp} />
        </div>
      </div>

      <FollowUpComposer onAsk={onAsk}
        busy={status === 'streaming' || status === 'connecting'}
        carries={liveIndex + (answer || result ? 1 : 0)} />
    </>
  );
}

function TurnCard({ index, total, turn, live, onFollowUp }) {
  const { formatClock } = usePreferences();
  const liveCtx = useInvestigation();
  const source = live ? liveCtx : turn;

  // Earlier turns start collapsed; the one being read stays open.
  const [open, setOpen] = useState(Boolean(live));

  const question = questionOf(source.request?.question);
  const answer = source.answer || source.result?.answer;
  const status = live ? liveCtx.status : (answer ? 'complete' : 'error');
  const failed = status === 'error';
  const seconds = ((live ? liveCtx.elapsed : source.elapsedMs) || 0) / 1000;

  return (
    <section className={`li-turn${live ? ' li-turn--live' : ''}`}>
      <header className="li-turn-head">
        <span className="li-turn-index" aria-hidden="true">{index + 1}</span>
        <div className="li-turn-question">
          <div className="li-turn-q-label">
            {index === 0 ? 'Asked' : 'Follow-up'} · {index + 1} of {total}
            {source.startedAt ? ` · ${formatClock(new Date(source.startedAt).toISOString())}` : ''}
            {seconds > 0 ? ` · ${seconds.toFixed(1)}s` : ''}
          </div>
          <p className="li-turn-q-text">{question}</p>
        </div>
        <span className="li-spacer" />
        <StatusChip status={status} live={live} />
        {live && (status === 'streaming' || status === 'connecting') && (
          <button type="button" className="li-turn-toggle li-turn-stop"
            onClick={liveCtx.stopInvestigation}>
            Stop
          </button>
        )}
        {!live && (
          <button type="button" className="li-turn-toggle" onClick={() => setOpen(!open)}
            aria-expanded={open}>
            {open ? 'Collapse' : 'Show analysis'}
          </button>
        )}
      </header>

      {/* Collapsed, an earlier turn still shows its conclusion — that is what
          makes a thread scannable. What it hides is the working. */}
      {!live && !open && (
        <div className={`li-turn-summary${failed ? ' li-turn-summary--failed' : ''}`}>
          <p className="li-turn-headline">
            {answer?.headline
              || source.errorDetail
              || 'This question did not produce an answer.'}
          </p>
          <div className="li-turn-facts">
            {answer?.root_cause_service && (
              <span className="li-chip li-chip--service">{answer.root_cause_service}</span>
            )}
            {typeof answer?.confidence === 'number' && (
              <span className="li-muted">confidence {(answer.confidence * 100).toFixed(0)}%</span>
            )}
            {source.llmUsage?.requests > 0 && (
              <span className="li-muted">{source.llmUsage.requests} LLM request
                {source.llmUsage.requests === 1 ? '' : 's'}</span>
            )}
            {source.trace?.length > 0 && (
              <span className="li-muted">
                {source.trace.filter((t) => t.type === 'action').length} tool call(s)
              </span>
            )}
          </div>
        </div>
      )}

      {(live || open) && (
        <div className="li-turn-body">
          <InvestigationResults turn={live ? undefined : turn} onFollowUp={onFollowUp}
            showHeader={false} />
        </div>
      )}
    </section>
  );
}

function StatusChip({ status, live }) {
  const tone = { complete: 'ok', error: 'err', streaming: 'accent', connecting: 'accent' }[status];
  const label = { complete: 'answered', error: 'failed', streaming: 'running', connecting: 'starting' }[status]
    || status;
  return (
    <span className={`li-turn-status li-turn-status--${tone || 'muted'}`}>
      {live && (status === 'streaming' || status === 'connecting') && (
        <span className="li-turn-pulse animate-pulse-glow" />
      )}
      {label}
    </span>
  );
}

/**
 * Asking the next question without leaving the thread.
 *
 * The form in the sidebar starts a *new* investigation — it resets the thread —
 * which left no obvious way to ask a second question about the same incident.
 * This one continues the thread, and says how much of it the agent will be told
 * about, because "does it remember what I just asked?" is otherwise unanswerable
 * from the screen.
 */
function FollowUpComposer({ onAsk, busy, carries }) {
  const [text, setText] = useState('');
  const boxRef = useRef(null);

  const submit = () => {
    const question = text.trim();
    if (!question || busy) return;
    setText('');
    onAsk(question);
  };

  // A textarea rather than an input, because questions to this agent are
  // sentences — "show me the 5xx errors in payment-api between 09:00 and 09:30
  // and explain what preceded them" does not fit in a one-line box, and not
  // being able to see what you typed is a poor way to ask it. Enter sends,
  // which is what a chat box is expected to do; Shift+Enter is the newline.
  // Bound explicitly rather than relying on a form's implicit submission, which
  // a textarea does not get at all.
  const onKeyDown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  const grow = (el) => {
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 132)}px`;
  };

  return (
    <form className="li-followup" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <label className="li-followup-label" htmlFor="li-followup-input">
        Ask a follow-up
        {carries > 0 && (
          <span className="li-muted">
            {' '}· the agent is given the previous {carries} answer{carries === 1 ? '' : 's'} as context
          </span>
        )}
      </label>
      <div className="li-followup-row">
        <textarea id="li-followup-input" className="input li-followup-box" rows={1}
          ref={(el) => { boxRef.current = el; grow(el); }}
          value={text} disabled={busy} onKeyDown={onKeyDown}
          placeholder={busy
            ? 'Waiting for the current answer…'
            : 'e.g. why did that service start failing? (Enter to send, Shift+Enter for a new line)'}
          onChange={(e) => { setText(e.target.value); grow(e.target); }} />
        <button type="submit" className="btn btn--primary" disabled={busy || !text.trim()}>
          Ask
        </button>
      </div>
    </form>
  );
}

/**
 * Alert-started investigations carry a JSON detection payload in the question.
 * The card shows it in full; the thread header wants the sentence only.
 */
function questionOf(raw) {
  if (!raw) return 'Investigation';
  const marker = 'Detection payload:';
  const idx = raw.indexOf(marker);
  return (idx === -1 ? raw : raw.slice(0, idx)).trim() || 'Investigation';
}
