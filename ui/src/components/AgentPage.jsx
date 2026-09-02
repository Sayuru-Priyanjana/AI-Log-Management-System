import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { deleteInvestigation, getRecentInvestigations, getSystems, getSystemIntegrations, notifyIntegrations } from '../api';
import { useInvestigation } from '../InvestigationContext';
import { useToast } from '../toast';
import { usePreferences } from '../preferences';
import InvestigationForm from './InvestigationForm';
import InvestigationResults from './InvestigationResults';

/**
 * The agent, full screen: recent chats, results, and the form that starts one.
 *
 * Kept separate from Workstation on purpose — that page is a dashboard glanced
 * at, this one is a working session with its own history. Reached three ways:
 * the "AI Agent" button (a blank form), a recent chat (a stored run reopened),
 * or an alert's "Investigate with agent" (a run started immediately, seeded
 * with that alert's payload).
 */
export default function AgentPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const toast = useToast();
  const { formatClock, formatDay } = usePreferences();
  const {
    request, result, status, meta, chatHistory, startInvestigation, loadInvestigation, clearInvestigation,
  } = useInvestigation();

  const nav = location.state || {};
  const [systems, setSystems] = useState([]);
  const [systemId, setSystemId] = useState(nav.system_id || localStorage.getItem('lastSystemId') || null);
  const [chats, setChats] = useState([]);
  const [loadingChats, setLoadingChats] = useState(true);
  const autoStarted = useRef(false);
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
    startInvestigation({
      system_id: systemId, environment: nav.environment, question: nav.question,
      service_hint: nav.service,
    }, { kind: nav.kind || 'new', label: nav.label, alertId: nav.alertId, auto: nav.auto });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [systemId]);

  // Handle auto-notification for scheduled scans and alerts when they complete
  useEffect(() => {
    if (status === 'complete' && result && (meta?.kind === 'scheduled' || (meta?.kind === 'alert' && meta?.auto)) && notifiedRef.current !== result.id) {
      notifiedRef.current = result.id;
      
      // Sync with UI: if it's an auto-investigated alert, mark it as handled and refresh sidebar
      if (meta?.kind === 'alert' && meta.alertId) {
        import('../mockData').then(({ setAlertStatus }) => {
          setAlertStatus(meta.alertId, 'handled');
        });
      }
      refreshChats(systemId);
      
      getSystemIntegrations(systemId)
        .then(({ values }) => {
          if (values?.notify_on_scan_result_enabled) {
            sendResultToIntegrations();
          }
        })
        .catch(console.error);
    } else if (status === 'complete' && result && meta?.kind === 'alert' && notifiedRef.current !== result.id) {
      // Manual alert investigations: just refresh chats and mark handled
      notifiedRef.current = result.id;
      if (meta.alertId) {
        import('../mockData').then(({ setAlertStatus }) => {
          setAlertStatus(meta.alertId, 'handled');
        });
      }
      refreshChats(systemId);
    } else if (status === 'complete' && result && notifiedRef.current !== result.id) {
      // Manual normal investigations: just refresh chats
      notifiedRef.current = result.id;
      refreshChats(systemId);
    }
  }, [status, result, meta, systemId]);

  const sendResultToIntegrations = async () => {
    if (!result) return;
    try {
      // Teams MessageCards are very strict and silently drop messages with unsupported markdown (like code blocks) or HTML tags
      const headline = result.answer?.headline || "Agent investigation complete.";
      const detail = result.answer?.detail || "";
      const rawText = `${headline}\n\n${detail}`;

      let text = rawText
        .replace(/```[a-z]*\n([\s\S]*?)```/gi, '\n$1\n') // Remove code block fences
        .replace(/`/g, '') // Remove inline code ticks
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

      if (text.length > 2000) text = text.substring(0, 2000) + "\n\n... (truncated for Teams)";
      
      const isIncident = rawText.toLowerCase().includes("incident detected") || rawText.toLowerCase().includes("root cause");
      const statusColor = isIncident ? "E81123" : "107C10";
      const statusText = isIncident ? "🔴 Incident Detected" : "🟢 No Anomalies";

      const payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": statusColor,
        "summary": "Agent Investigation Result",
        "title": `Agent Result: ${meta?.label || 'Investigation'}`,
        "sections": [
          {
            "facts": [
              {
                "name": "Status:",
                "value": statusText
              },
              {
                "name": "Target:",
                "value": selected?.name || systemId || 'Unknown'
              }
            ],
            "markdown": true
          },
          {
            "activityTitle": "**Executive Summary**",
            "text": text,
            "markdown": true
          }
        ]
      };
      const response = await notifyIntegrations(systemId, payload);
      if (response && response.ok === false) {
        throw new Error(response.detail || "Teams integration returned an error");
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

  const startNew = (payload) => startInvestigation(payload, { kind: 'new' });

  const openChat = (id) => {
    if (request?.id === id) return;
    loadInvestigation(id);
  };

  const removeChat = async (event, id) => {
    event.stopPropagation();
    try {
      await deleteInvestigation(id);
      setChats((c) => c.filter((i) => i.id !== id));
      if (result?.id === id) clearInvestigation();
      toast.success('Deleted');
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
        {status === 'complete' && result && (
          <button type="button" className="btn btn--sm" onClick={sendResultToIntegrations}>
            Send result to integrations
          </button>
        )}
        {chats.length > 0 && (
          <button type="button" className="btn btn--sm" onClick={() => clearInvestigation()}>
            New chat
          </button>
        )}
      </div>

      <div className="agent-body">
        <aside className="agent-chats">
          <div className="ws-side-head"><h4>Recent chats</h4></div>
          <div className="agent-chats-list">
            {loadingChats && <div className="empty">Loading…</div>}
            {!loadingChats && chats.length === 0 && <div className="empty">No investigations yet.</div>}
            {chats.map((chat) => (
              <button key={chat.id} type="button"
                className={`agent-chat-item ${request?.id === chat.id ? 'is-active' : ''}`}
                onClick={() => openChat(chat.id)}>
                <div className="agent-chat-label">{chat.question || 'Investigation'}</div>
                <div className="agent-chat-meta">
                  {formatDay(chat.created_at)} {formatClock(chat.created_at)}
                </div>
                <span className="agent-chat-delete" role="button" tabIndex={-1}
                  onClick={(e) => removeChat(e, chat.id)} title="Delete">×</span>
              </button>
            ))}
          </div>
        </aside>

        <section className="agent-results">
          {!request ? (
            <div className="empty" style={{ marginTop: 40 }}>
              Fill in the investigation panel and click Ask AI.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
              {chatHistory && chatHistory.map((pastChat, idx) => (
                <div key={idx} style={{ padding: '16px', background: 'var(--bg-card)', borderRadius: '8px', border: '1px solid var(--border)' }}>
                  <details>
                    <summary style={{ fontWeight: 'bold', cursor: 'pointer' }}>
                      Previous Turn: {pastChat.request?.question || 'Investigation'}
                    </summary>
                    <div style={{ marginTop: '10px' }}>
                      <p><strong>Answer:</strong> {pastChat.answer?.headline || (pastChat.result?.answer?.headline)}</p>
                      <p>{pastChat.answer?.detail || (pastChat.result?.answer?.detail)}</p>
                    </div>
                  </details>
                </div>
              ))}
              <InvestigationResults
                onFollowUp={(question) => startInvestigation({ ...request, question, _at: Date.now() },
                  { kind: 'followup' })} />
            </div>
          )}
        </section>

        <aside className="agent-entry">
          <h3 style={{ marginBottom: 10 }}>Investigation</h3>
          {!selected ? (
            <div className="empty">No system selected.</div>
          ) : (
            <>
              {systems.length > 1 && (
                <div className="field" style={{ marginBottom: 10 }}>
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
        </aside>
      </div>
    </div>
  );
}
