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
export default function ConversationThread({ onFollowUp, onAsk, onSend }) {
  return (
    <div className="li-conversation">
      <Thread onFollowUp={onFollowUp} onAsk={onAsk} onSend={onSend} />
    </div>
  );
}

function Thread({ onFollowUp, onAsk, onSend }) {
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
            onFollowUp={onFollowUp} onSend={onSend} />
        ))}

        <div ref={liveRef}>
          <TurnCard index={liveIndex} total={liveIndex + 1} live
            onFollowUp={onFollowUp} onSend={onSend} />
        </div>
      </div>

      <FollowUpComposer onAsk={onAsk}
        busy={status === 'streaming' || status === 'connecting'}
        carries={liveIndex + (answer || result ? 1 : 0)} />
    </>
  );
}

/**
 * One exchange, laid out as a chat.
 *
 * The turn used to be a document section: a numbered heading, the question set
 * as a title, and the analysis beneath it. That reads as a report, not as a
 * conversation, and a thread of them gives no sense of who said what.
 *
 * Here the question is a message from the person and the analysis is a reply
 * from the agent, each with a speaker. It is the same information — the turn
 * number, the time, how long it took, whether it is still running — but placed
 * where a chat puts it, so the thread scans as an exchange.
 */
function TurnCard({ index, total, turn, live, onFollowUp, onSend }) {
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
  const asked = source.startedAt
    ? formatClock(new Date(source.startedAt).toISOString()) : null;
  const working = live && (status === 'streaming' || status === 'connecting');

  return (
    <section className={`li-turn${live ? ' li-turn--live' : ''}`}>
      {/* the question, from the person asking */}
      <div className="li-msg li-msg--user">
        <span className="li-msg-avatar li-msg-avatar--user" aria-hidden="true">You</span>
        <div className="li-msg-body">
          <div className="li-msg-meta">
            <span className="li-msg-who">You</span>
            {asked && <span>{asked}</span>}
            <span>·</span>
            <span>{index === 0 ? 'first question' : `follow-up ${index}`} of {total}</span>
          </div>
          <p className="li-msg-bubble">{question}</p>
        </div>
      </div>

      {/* and the agent's reply */}
      <div className="li-msg li-msg--agent">
        <span className={`li-msg-avatar li-msg-avatar--agent${working ? ' is-working' : ''}`}
          aria-hidden="true">AI</span>
        <div className="li-msg-body">
          <div className="li-msg-meta">
            <span className="li-msg-who">LogIntel agent</span>
            {seconds > 0 && <span>answered in {seconds.toFixed(1)}s</span>}
            <StatusChip status={status} live={live} />
            <span className="li-spacer" />
            {working && (
              <button type="button" className="li-turn-toggle li-turn-stop"
                onClick={liveCtx.stopInvestigation}>
                Stop
              </button>
            )}
            {onSend && source.result && answer && (
              <button type="button" className="li-turn-toggle li-turn-send"
                onClick={() => onSend(source.result)}
                title="Post this answer to the configured Teams channel">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                  strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
                </svg>
                Send
              </button>
            )}
            {!live && (
              <button type="button" className="li-turn-toggle" onClick={() => setOpen(!open)}
                aria-expanded={open}>
                {open ? 'Collapse' : 'Show analysis'}
              </button>
            )}
          </div>

          {/* Collapsed, an earlier reply still shows its conclusion — that is
              what makes a thread scannable. What it hides is the working. */}
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
        </div>
      </div>
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
      <div className="li-followup-box-wrap">
        <textarea id="li-followup-input" className="li-followup-box" rows={1}
          ref={(el) => { boxRef.current = el; grow(el); }}
          value={text} disabled={busy} onKeyDown={onKeyDown}
          placeholder={busy
            ? 'Waiting for the current answer…'
            : 'Ask a follow-up about this investigation…'}
          onChange={(e) => { setText(e.target.value); grow(e.target); }} />
        <button type="submit" className="li-followup-send"
          disabled={busy || !text.trim()} aria-label="Send">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M5 12h14M13 6l6 6-6 6" />
          </svg>
        </button>
      </div>
      <div className="li-followup-foot">
        {carries > 0 && (
          <span className="li-followup-memory" title={
            'Each follow-up is sent with the questions and answers above, and the '
            + 'agent keeps its own copy of the thread, so it can resolve "that '
            + 'service" or "why did that happen". The run summary reports how many '
            + 'turns it recalled.'}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2.4" aria-hidden="true">
              <path d="M21 12a9 9 0 11-6.2-8.6" strokeLinecap="round" />
              <path d="M12 7v5l3 2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {/* "the previous 1 answer" reads as though an earlier turn is
                being recalled, when on the first question the one answer it
                means is the one on screen. Say what the next question will
                actually carry. */}
            {carries === 1
              ? 'Your next question includes the answer above'
              : `Your next question includes all ${carries} answers above`}
          </span>
        )}
        <span className="li-spacer" />
        <span className="li-followup-kbd">Enter to send · Shift+Enter for a new line</span>
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
