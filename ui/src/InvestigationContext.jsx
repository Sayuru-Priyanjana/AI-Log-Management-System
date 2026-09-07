import { createContext, useContext, useState, useRef, useEffect } from 'react';
import { getInvestigation, getInvestigationThread, runInvestigation } from './api';

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

/**
 * The thread, in the shape the agent reads it.
 *
 * Three things matter here beyond "put the turns in a list":
 *
 *  - it is bounded. Every turn is prepended to a prompt that already carries
 *    the evidence, and this agent's context window is a real ceiling: Ollama
 *    truncates past it silently, dropping the *head* of the prompt, so an
 *    unbounded history does not degrade the answer gradually — it removes the
 *    schema and the evidence and leaves the model answering from the tail.
 *    Only the most recent turns are carried, each one trimmed.
 *  - the assistant turn carries the conclusion, not the prose. Headline plus a
 *    trimmed detail plus the service that was blamed is what a follow-up like
 *    "why did that happen?" actually needs; the full narrative would crowd out
 *    the evidence for the question being asked now.
 *  - a turn that never produced an answer still says so. Dropping it would let
 *    "and the one before that?" refer to a turn the model was never told about.
 */
function buildChatHistory(turns, { maxTurns = 6, maxAnswerChars = 700 } = {}) {
  return turns.slice(-maxTurns).flatMap((turn) => {
    const question = turn.request?.question?.trim();
    if (!question) return [];

    const answer = turn.answer || turn.result?.answer;
    let content;
    if (answer?.headline) {
      const parts = [answer.headline.trim()];
      if (answer.root_cause_service) parts.push(`Service identified: ${answer.root_cause_service}.`);
      if (answer.detail) parts.push(trim(answer.detail.trim(), maxAnswerChars));
      content = parts.join(' ');
    } else {
      content = 'This question did not produce an answer.';
    }
    return [
      { role: 'user', content: question },
      { role: 'assistant', content },
    ];
  });
}

function trim(text, limit) {
  return text.length <= limit ? text : `${text.slice(0, limit).trimEnd()}…`;
}


export function InvestigationProvider({ children }) {
  const [request, setRequest] = useState(null);
  const [stages, setStages] = useState({});
  const [trace, setTrace] = useState([]);
  const [answer, setAnswer] = useState(null);
  const [evidenceTimeline, setEvidenceTimeline] = useState(null);
  const [result, setResult] = useState(null);
  // What the model cost this run: request count, model name, context window.
  // Streamed as its own stage so it is on screen even when a run is stopped
  // before it is stored.
  const [llmUsage, setLlmUsage] = useState(null);
  // The agent workflow's shape, sent once at the start of a run, plus which
  // nodes have finished so far. Only the LangGraph backend streams branch
  // decisions; the deterministic one has no branches to record.
  const [graph, setGraph] = useState(null);
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

  // The thread, held in a ref as well as in state.
  //
  // `chatHistory` state is what renders; this is what the next request is built
  // from, and it has to be readable *synchronously*. The previous version
  // computed the outgoing history inside a `setChatHistory` updater and read
  // the local it assigned on the next line — but React does not run an updater
  // synchronously, so that local was still `[]` every time and no follow-up
  // ever carried the earlier turns. The model was answering each question in
  // the thread as if it were the first. StrictMode made it worse: when the
  // updater did run, it ran twice, appending the same turn twice.
  const historyRef = useRef([]);
  // The conversation these turns belong to, server-side.
  //
  // Without it every turn was stored as an unrelated document: a seven-question
  // conversation came back as seven entries in Recent chats, and reopening any
  // of them replayed that turn alone. The thread only ever existed in the tab
  // that created it, so it did not survive a reload — let alone the next day.
  const threadRef = useRef(null);
  // A live mirror of the turn currently on screen, so snapshotting it does not
  // depend on which render's closure `startInvestigation` was created in.
  const liveTurnRef = useRef(null);


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

  useEffect(() => {
    if (!request) { liveTurnRef.current = null; return; }
    liveTurnRef.current = {
      request, result, trace, answer, stages, evidenceTimeline, llmUsage, graph,
      meta, status,
      startedAt: startedAt.current,
      elapsedMs: elapsed,
    };
  }, [request, result, trace, answer, stages, evidenceTimeline, llmUsage, graph,
      meta, status, elapsed]);

  const startInvestigation = async (newRequest, investigationMeta = { kind: 'new' }) => {
    if (controllerRef.current) controllerRef.current.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    // Only an explicit follow-up continues the thread. Testing for "not new"
    // instead would fold an alert or a scheduled scan into whatever conversation
    // happened to be on screen — an unrelated incident appended as turn 4, and
    // its answer handed to the agent as context for the next question.
    const continuing = investigationMeta.kind === 'followup' && Boolean(liveTurnRef.current);
    if (continuing) {
      historyRef.current = [...historyRef.current, liveTurnRef.current];
    } else {
      historyRef.current = [];
    }
    setChatHistory(historyRef.current);

    // A follow-up stays in the thread; anything else opens one. The id is
    // provisional until the first turn is stored, at which point the backend
    // echoes back the thread it actually used.
    if (!continuing || !threadRef.current) {
      threadRef.current = `thr-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    }

    const payload = {
      ...newRequest,
      thread_id: threadRef.current,
      chat_history: buildChatHistory(historyRef.current),
    };

    setRequest(payload);
    setStages({});
    setTrace([]);
    setAnswer(null);
    setResult(null);
    setEvidenceTimeline(null);
    setLlmUsage(null);
    setGraph(null);
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
          } else if (stage === 'graph') {
            setGraph(data);
          } else if (stage === 'llm') {
            setLlmUsage(data);
          } else if (stage === 'answer') {
            setAnswer(data);
          } else if (stage === 'evidence_timeline') {
            setEvidenceTimeline(data);
          } else if (stage === 'result') {
            setResult(data);
            if (data?.llm) setLlmUsage(data.llm);
            if (data?.thread_id) threadRef.current = data.thread_id;
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
    historyRef.current = [];
    liveTurnRef.current = null;
    threadRef.current = null;
    setRequest(null);
    setStatus('idle');
    setChatHistory([]);
    setMeta({ kind: 'new' });
  };

  /**
   * Turns one stored run into the shape a rendered turn expects.
   *
   * The raw ReAct trace is not persisted — only the verified answer is — so
   * `trace` stays empty and the answer panel carries the reasoning. Everything
   * else survives, which is why a reopened turn still shows its window, its
   * signals, its evidence timeline and what the model cost.
   */
  const turnFromStored = (stored) => ({
    request: {
      system_id: stored.plan?.system_id,
      environment: stored.plan?.environment,
      question: stored.question,
      service_hint: stored.plan?.service,
    },
    stages: {
      plan: stored.plan,
      windows: stored.windows,
      signals: { signals: stored.signals || [], count: (stored.signals || []).length },
      candidates: { candidates: stored.candidates || [] },
    },
    trace: [],
    answer: stored.answer,
    evidenceTimeline: {
      window: stored.windows?.incident,
      baseline: stored.windows?.baseline,
      entries: stored.evidence_timeline || [],
      collapsed_from: stored.evidence_summary?.logs?.total_documents,
    },
    result: stored,
    llmUsage: stored.llm || null,
    graph: (stored.graph_path || []).length
      ? { engine: 'langgraph', path: stored.graph_path, decisions: stored.graph_decisions || [] }
      : null,
    status: 'complete',
    startedAt: stored.created_at ? new Date(stored.created_at).getTime() : 0,
    elapsedMs: Object.values(stored.timings_ms || {}).reduce((a, b) => a + b, 0),
    errorDetail: null,
    meta: { kind: 'new' },
  });

  const applyTurn = (turn) => {
    setRequest(turn.request);
    setStages(turn.stages);
    setTrace(turn.trace);
    setAnswer(turn.answer);
    setEvidenceTimeline(turn.evidenceTimeline);
    setResult(turn.result);
    setLlmUsage(turn.llmUsage);
    setGraph(turn.graph);
    setElapsed(turn.elapsedMs);
    setStatus('complete');
  };

  /**
   * Reopens a whole conversation, not just one of its questions.
   *
   * Accepts either a single investigation id or the list of turn ids a Recent
   * chats row carries. Loading one turn of a seven-question thread and calling
   * it "the chat" is what made yesterday's conversation look like it had lost
   * everything but its last answer.
   */
  const loadInvestigation = async (idOrIds) => {
    stopInvestigation();
    const ids = Array.isArray(idOrIds) ? idOrIds.filter(Boolean) : [idOrIds];
    historyRef.current = [];
    liveTurnRef.current = null;
    threadRef.current = null;
    setStatus('connecting');
    setErrorDetail(null);
    setLlmUsage(null);
    setGraph(null);
    try {
      const stored = ids.length > 1
        ? (await getInvestigationThread(ids)).turns || []
        : [await getInvestigation(ids[0])];
      if (!stored.length) throw new Error('That conversation could not be found');

      const turns = stored.map(turnFromStored);
      const last = turns[turns.length - 1];

      // The earlier turns become the thread; the most recent one is what the
      // page shows as current, exactly as if it had just finished running.
      historyRef.current = turns.slice(0, -1);
      liveTurnRef.current = last;
      threadRef.current = stored[stored.length - 1]?.thread_id || null;
      setChatHistory(historyRef.current);

      const lastId = stored[stored.length - 1]?.id;
      setMeta(readMeta()[lastId] || { kind: 'new' });
      setTrace([]);
      applyTurn(last);
    } catch (err) {
      setStatus('error');
      setErrorDetail(err.message || 'Could not load that investigation');
    }
  };

  return (
    <InvestigationContext.Provider
      value={{
        request, stages, trace, answer, evidenceTimeline, result,
        llmUsage, graph,
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
