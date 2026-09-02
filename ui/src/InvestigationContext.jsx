import { createContext, useContext, useState, useRef, useEffect } from 'react';
import { getInvestigation, runInvestigation } from './api';

const InvestigationContext = createContext(null);

const META_KEY = 'investigationMeta';

// What kind of investigation this was, and the label that goes with it — an
// alert's title, or a scheduled run's timestamp. The backend has no concept of
// this (every investigation is just a question), so it is recorded client-side,
// keyed by the id the backend assigns once the run is saved. Read back when a
// past investigation is reopened from Recent Chats so its title still makes
// sense after a reload.
function readMeta() {
  try {
    return JSON.parse(localStorage.getItem(META_KEY) || '{}');
  } catch {
    return {};
  }
}

function writeMeta(id, meta) {
  const all = readMeta();
  all[id] = meta;
  // Bounded, so a long-lived tab does not grow this without limit.
  const ids = Object.keys(all);
  if (ids.length > 200) delete all[ids[0]];
  localStorage.setItem(META_KEY, JSON.stringify(all));
}

export function InvestigationProvider({ children }) {
  const [request, setRequest] = useState(null);
  const [stages, setStages] = useState({});
  const [trace, setTrace] = useState([]);
  const [answer, setAnswer] = useState(null);
  const [evidenceTimeline, setEvidenceTimeline] = useState(null);
  const [result, setResult] = useState(null);
  const [status, setStatus] = useState('idle'); // idle, connecting, streaming, complete, error
  const [elapsed, setElapsed] = useState(0);
  const [errorDetail, setErrorDetail] = useState(null);
  const [chatHistory, setChatHistory] = useState([]); // stores past turns in a single thread
  // { kind: 'new' | 'scheduled' | 'alert', label?: string } — label is the
  // alert name or the scheduled timestamp; absent for an ordinary question.
  const [meta, setMeta] = useState({ kind: 'new' });

  const startedAt = useRef(0);
  const controllerRef = useRef(null);
  const metaRef = useRef({ kind: 'new' });

  useEffect(() => {
    const tick = setInterval(() => {
      setStatus((s) => {
        if (s === 'connecting' || s === 'streaming') {
          setElapsed(Date.now() - startedAt.current);
        }
        return s;
      });
    }, 250);
    return () => clearInterval(tick);
  }, []);

  const startInvestigation = async (newRequest, investigationMeta = { kind: 'new' }) => {
    if (controllerRef.current) controllerRef.current.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    // If it's a follow-up on the SAME system and environment, append to chatHistory
    // Otherwise, clear the chatHistory
    let updatedHistory = [];
    setChatHistory(prev => {
      if (investigationMeta.kind !== 'new' && request) {
        updatedHistory = [...prev, { request, result, trace, answer, stages, evidenceTimeline }];
        return updatedHistory;
      }
      return [];
    });

    const payload = {
      ...newRequest,
      chat_history: updatedHistory.map(turn => ({
        role: 'user', content: turn.request.question
      })).concat(updatedHistory.map(turn => ({
        role: 'assistant', content: turn.answer?.headline || ''
      }))).sort((a, b) => 0) // simplistic merge - actually let's interleave them correctly
    };
    
    // Better interleaving
    payload.chat_history = updatedHistory.flatMap(turn => [
      { role: 'user', content: turn.request.question },
      { role: 'assistant', content: turn.answer?.headline + '\n' + (turn.answer?.detail || '') }
    ]);

    setRequest(payload);
    setStages({});
    setTrace([]);
    setAnswer(null);
    setResult(null);
    setEvidenceTimeline(null);
    setStatus('connecting');
    setErrorDetail(null);
    setElapsed(0);
    setMeta(investigationMeta);
    metaRef.current = investigationMeta;
    startedAt.current = Date.now();

    try {
      await runInvestigation(payload, {
        signal: controller.signal,
        onEvent: (event) => {
          if (controllerRef.current !== controller) return;
          const { stage, data } = event;

          if (stage === 'error') {
            setStatus('error');
            setErrorDetail(data?.detail || 'Investigation failed');
            return;
          }
          setStatus('streaming');

          if (stage === 'reasoning') {
            setTrace((prev) => [...prev, data]);
          } else if (stage === 'answer') {
            setAnswer(data);
          } else if (stage === 'evidence_timeline') {
            setEvidenceTimeline(data);
          } else if (stage === 'result') {
            setResult(data);
            // The id only exists now. Record what kind of run this was against
            // it, so reopening it later from history still shows the right title.
            if (data?.id && metaRef.current.kind !== 'new') {
              writeMeta(data.id, metaRef.current);
            }
          } else {
            setStages((prev) => ({ ...prev, [stage]: data }));
          }
        },
      });
      if (controllerRef.current === controller) {
        setStatus((s) => (s === 'error' ? s : 'complete'));
      }
    } catch (err) {
      if (controllerRef.current !== controller && controllerRef.current !== null) {
        // Superseded by a new investigation; do not overwrite its state
        return;
      }
      setStatus('error');
      setErrorDetail(err.name === 'AbortError' ? 'Investigation aborted' : (err.message || 'Investigation failed'));
    }
  };

  const stopInvestigation = () => {
    if (controllerRef.current) {
      controllerRef.current.abort();
      controllerRef.current = null;
    }
  };

  const clearInvestigation = () => {
    stopInvestigation();
    setRequest(null);
    setStatus('idle');
    setChatHistory([]);
    setMeta({ kind: 'new' });
  };

  // Reopens a finished run from storage rather than re-asking the model. The
  // raw ReAct trace (thoughts, tool calls) is not persisted server-side — only
  // the final answer is — so `trace` stays empty and the answer panel is
  // what carries the reasoning here.
  const loadInvestigation = async (id) => {
    stopInvestigation();
    setStatus('connecting');
    setErrorDetail(null);
    try {
      const stored = await getInvestigation(id);
      const savedMeta = readMeta()[id] || { kind: 'new' };
      setMeta(savedMeta);
      setRequest({
        system_id: stored.plan?.system_id,
        environment: stored.plan?.environment,
        question: stored.question,
        service_hint: stored.plan?.service,
      });
      setStages({
        plan: stored.plan,
        windows: stored.windows,
        signals: { signals: stored.signals || [], count: (stored.signals || []).length },
        candidates: { candidates: stored.candidates || [] },
      });
      setChatHistory([]);
      setTrace([]);
      setAnswer(stored.answer);
      setEvidenceTimeline({
        window: stored.windows?.incident,
        baseline: stored.windows?.baseline,
        entries: stored.evidence_timeline || [],
        collapsed_from: stored.evidence_summary?.logs?.total_documents,
      });
      setResult(stored);
      setElapsed(Object.values(stored.timings_ms || {}).reduce((a, b) => a + b, 0));
      setStatus('complete');
    } catch (err) {
      setStatus('error');
      setErrorDetail(err.message || 'Could not load that investigation');
    }
  };

  return (
    <InvestigationContext.Provider
      value={{
        request, stages, trace, answer, evidenceTimeline, result,
        status, elapsed, errorDetail, meta, chatHistory,
        startInvestigation, stopInvestigation, clearInvestigation, loadInvestigation,
        setRequest,
      }}
    >
      {children}
    </InvestigationContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useInvestigation() {
  const context = useContext(InvestigationContext);
  if (!context) {
    throw new Error('useInvestigation must be used within an InvestigationProvider');
  }
  return context;
}
