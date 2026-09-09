import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { deleteInvestigation, getRecentInvestigations, getSystems, getSystemIntegrations, notifyIntegrations } from '../api';
import { setAlertStatus } from '../mockData';
import { useInvestigation } from '../InvestigationContext';
import { useToast } from '../toast';
import { investigationCard } from '../teams';
import { usePreferences } from '../preferences';
import InvestigationForm from './InvestigationForm';
import ConversationThread from './ConversationThread';
import { Resizer, useResizableWidth } from './ResizablePane';

/**
 * The agent, full screen: recent chats, results, and the form that starts one.
 *
 * Kept separate from Workstation on purpose — that page is a dashboard glanced
 * at, this one is a working session with its own history. Reached three ways:
 * the "AI Agent" button (a blank form), a recent chat (a stored run reopened),
 * or an alert's "Investigate with agent" (a run started immediately, seeded
 * with that alert's payload).
 */
/**
 * Conversations bucketed into Today / Yesterday / the day they happened.
 *
 * A flat list of forty rows makes "the conversation I had yesterday" a
 * scrolling exercise — and yesterday is exactly how people refer to a past
 * investigation. The list is already newest-first, so a single pass keeps that
 * order within each day.
 */
function groupByDay(chats, formatDay) {
  const today = new Date().toDateString();
  const yesterday = new Date(Date.now() - 86400000).toDateString();
  const groups = [];
  const seen = new Map();

  for (const chat of chats) {
    const when = chat.created_at ? new Date(chat.created_at) : null;
    const day = when ? when.toDateString() : 'unknown';
    const label = day === today ? 'Today'
      : day === yesterday ? 'Yesterday'
        : (when ? formatDay(chat.created_at) : 'Earlier');
    let group = seen.get(label);
    if (!group) {
      group = { label, items: [] };
      seen.set(label, group);
      groups.push(group);
    }
    group.items.push(chat);
  }
  return groups;
}

export default function AgentPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const toast = useToast();
  const { formatClock, formatDay, formatStamp } = usePreferences();
  const {
    request, result, status, meta, chatHistory, startInvestigation, loadInvestigation, clearInvestigation,
  } = useInvestigation();

  const nav = location.state || {};
  const chatsPane = useResizableWidth('ui.agentChatsWidth', 220, [160, 460]);
  const entryPane = useResizableWidth('ui.agentEntryWidth', 300, [240, 560]);
  const [systems, setSystems] = useState([]);
  const [systemId, setSystemId] = useState(nav.system_id || localStorage.getItem('lastSystemId') || null);
  const [chats, setChats] = useState([]);
  const [loadingChats, setLoadingChats] = useState(true);
  const autoStarted = useRef(false);
  const [openThread, setOpenThread] = useState(null);
  // The ask form is shown when there is no conversation on screen — the way a
  // chat app shows its empty state and then gets out of the way. "New chat"
  // clears the conversation, which brings it back.
  const composing = !request;
  const notifiedRef = useRef(null);

  useEffect(() => {
    getSystems()
      .then((res) => {
        const list = res.systems || [];
        setSystems(list);
        setSystemId((current) => current || list[0]?.id || null);
      })
      .catch((err) => toast.error('Could not load systems', { detail: err.message }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshChats = (id) => {
    if (!id) return;
    setLoadingChats(true);
    getRecentInvestigations(50, id)
      .then((res) => setChats(res.investigations || []))
      .catch((err) => toast.error('Could not load recent chats', { detail: err.message }))
      .finally(() => setLoadingChats(false));
  };

  useEffect(() => { refreshChats(systemId); }, [systemId]); // eslint-disable-line react-hooks/exhaustive-deps

  // An alert hands over a question and expects the investigation to start
  // immediately — that is the whole point of the "Investigate with agent"
  // button. Guarded so a later re-render (or the effect re-running after
  // systems load) does not fire it a second time.
  useEffect(() => {
    if (autoStarted.current || !systemId) return;
    if (nav.investigation_id) {
      autoStarted.current = true;
      loadInvestigation(nav.investigation_id);
      return;
    }
    if (!nav.question) return;
    autoStarted.current = true;
    // `start_time`/`end_time` are forwarded when the caller supplied them. An
    // alert knows when it fired, and without this the investigation fell back to
    // the default lookback — so a detection from three hours ago was diagnosed
    // against the last hour, in which nothing had happened, and came back
    // all-clear on an alert that was still open.
    startInvestigation({
      system_id: systemId, environment: nav.environment, question: nav.question,
      service_hint: nav.service,
      start_time: nav.start_time, end_time: nav.end_time,
    }, { kind: nav.kind || 'new', label: nav.label, alertId: nav.alertId, auto: nav.auto });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [systemId]);

  // Handle UI sync and chat refresh when investigations complete
  useEffect(() => {
    if (status === 'complete' && result && notifiedRef.current !== result.id) {
      notifiedRef.current = result.id;
      if (meta?.kind === 'alert' && meta.alertId) {
        setAlertStatus(meta.alertId, 'handled');
      }
      refreshChats(systemId);
    }
  }, [status, result, meta, systemId]);

  const sendResultToIntegrations = async (which) => {
    const target = which || result;
    if (!target) return;
    try {
      const response = await notifyIntegrations(systemId, investigationCard({
        result: target,
        systemName: selected?.name,
        systemId,
        label: meta?.label,
        formatStamp,
      }));
      if (response && response.ok === false) {
        throw new Error(response.detail || 'Teams integration returned an error');
      }
      toast.success('Sent result to integrations');
    } catch (err) {
      toast.error('Could not send to integrations', { detail: err.message });
    }
  };

  const selected = systems.find((s) => s.id === systemId);

  const title = useMemo(() => {
    if (meta?.kind === 'alert' && meta.label) return `${meta.label} Investigation`;
    if (meta?.kind === 'scheduled' && meta.label) return `Scheduled investigation — ${meta.label}`;
    if (request) return `New investigation on ${selected?.name || systemId}`;
    return selected ? `Investigate ${selected.name}` : 'Investigate';
  }, [meta, request, selected, systemId]);

  const startNew = (payload) => {
    // A new question is not part of whichever conversation is open.
    setOpenThread(null);
    return startInvestigation(payload, { kind: 'new' });
  };

  // A row is a conversation. Opening it replays every turn it holds, so a
  // seven-question thread comes back as seven turns rather than its last one.
  const openChat = (chat) => {
    const ids = chat.ids?.length ? chat.ids : [chat.id];
    if (openThread === chat.id) return;
    setOpenThread(chat.id);
    loadInvestigation(ids);
  };

  const removeChat = async (event, chat) => {
    event.stopPropagation();
    const ids = chat.ids?.length ? chat.ids : [chat.id];
    try {
      // Deleting a conversation deletes its turns; leaving the others behind
      // would resurrect the thread as loose fragments on the next refresh.
      await Promise.all(ids.map((id) => deleteInvestigation(id)));
      setChats((c) => c.filter((i) => i.id !== chat.id));
      if (openThread === chat.id) { setOpenThread(null); clearInvestigation(); }
      toast.success(ids.length > 1 ? `Deleted ${ids.length} questions` : 'Deleted');
    } catch (err) {
      toast.error('Could not delete', { detail: err.message });
    }
  };

  return (
    <div className="agent">
      <div className="agent-bar">
        <button type="button" className="btn btn--sm" onClick={() => navigate('/')}>← Back</button>
        <h2>{title}</h2>
        {selected && <span className="chip chip--mono">{selected.id}</span>}
        {meta?.kind === 'alert' && nav.serviceLabel && (
          <span className="chip">{nav.serviceLabel}</span>
        )}
        {chats.length > 0 && (
          <button type="button" className="btn btn--sm" onClick={() => clearInvestigation()}>
            New chat
          </button>
        )}
      </div>

      <div className="agent-body"
        style={{ gridTemplateColumns: composing
          ? `${chatsPane.width}px 6px minmax(0, 1fr) 6px ${entryPane.width}px`
          : `${chatsPane.width}px 6px minmax(0, 1fr)` }}>
        <aside className="agent-chats">
          <header className="agent-chats-head">
            <h4>Conversations</h4>
            <button type="button" className="agent-chats-new" onClick={() => {
              setOpenThread(null);
              clearInvestigation();
            }} title="Start a new conversation">+ New chat</button>
          </header>
          <div className="agent-chats-list">
            {loadingChats && <div className="empty">Loading…</div>}
            {!loadingChats && chats.length === 0 && (
              <div className="empty">No conversations yet.<br />Ask a question to start one.</div>
            )}
            {/* Grouped by day. A flat list of forty rows makes "the one I had
                yesterday" a scrolling exercise, and yesterday is exactly how
                people refer to a past investigation. */}
            {groupByDay(chats, formatDay).map(({ label, items }) => (
              <div key={label} className="agent-chats-group">
                <div className="agent-chats-day">{label}</div>
                {items.map((chat) => (
                  <button key={chat.id} type="button"
                    className={`agent-chat-item ${openThread === chat.id ? 'is-active' : ''}`}
                    onClick={() => openChat(chat)}>
                    <div className="agent-chat-top">
                      <span className={`agent-chat-dot agent-chat-dot--${
                        chat.analysis?.incident_detected ? (chat.analysis.severity || 'high') : 'none'}`}
                        aria-hidden="true" />
                      <span className="agent-chat-label">{chat.question || 'Investigation'}</span>
                    </div>
                    <div className="agent-chat-meta">
                      <span>{formatClock(chat.created_at)}</span>
                      {/* What tells a conversation apart from a one-off. */}
                      {chat.turn_count > 1 && (
                        <span className="agent-chat-turns">{chat.turn_count} questions</span>
                      )}
                      {chat.service && <span className="agent-chat-svc">{chat.service}</span>}
                    </div>
                    <span className="agent-chat-delete" role="button" tabIndex={-1}
                      onClick={(e) => removeChat(e, chat)}
                      title={chat.turn_count > 1
                        ? `Delete this conversation (${chat.turn_count} questions)`
                        : 'Delete'}>×</span>
                  </button>
                ))}
              </div>
            ))}
          </div>
        </aside>

        <Resizer handle={chatsPane} edge="left" label="Resize the recent chats panel" />

        <section className="agent-results">
          {!request ? (
            <div className="empty" style={{ marginTop: 40 }}>
              Fill in the investigation panel and click Ask AI. Follow-up questions
              stay in the same thread and carry the earlier answers as context.
            </div>
          ) : (
            <ConversationThread
              onAsk={(question) => startInvestigation(
                { ...request, question, _at: Date.now() }, { kind: 'followup' })}
              onFollowUp={(question) => startInvestigation(
                { ...request, question, _at: Date.now() }, { kind: 'followup' })}
              onSend={sendResultToIntegrations} />
          )}
        </section>

        {composing && (
          <>
            <Resizer handle={entryPane} edge="right" label="Resize the investigation panel" />
            <aside className="agent-entry">
              <header className="agent-entry-head">
                <h3>New investigation</h3>
              </header>
              <div className="agent-entry-body">
                {!selected ? (
                  <div className="empty">No system selected.</div>
                ) : (
                  <>
                    {systems.length > 1 && (
                      <div className="field" style={{ marginBottom: 12 }}>
                        <label htmlFor="agent-system">System</label>
                        <select id="agent-system" className="input" value={systemId}
                          onChange={(e) => { setSystemId(e.target.value); localStorage.setItem('lastSystemId', e.target.value); }}>
                          {systems.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                        </select>
                      </div>
                    )}
                    <InvestigationForm onSubmit={startNew} lockedSystem={selected}
                      initial={status === 'idle' ? nav : undefined} submitLabel="Ask AI" />
                  </>
                )}
              </div>
            </aside>
          </>
        )}
      </div>
    </div>
  );
}
