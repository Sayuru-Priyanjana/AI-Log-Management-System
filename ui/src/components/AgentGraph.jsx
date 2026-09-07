import { useEffect, useMemo, useState } from 'react';
import { getAgentGraph } from '../api';
import { useInvestigation } from '../InvestigationContext';

/**
 * The agent's workflow, drawn while it runs and re-read after it finishes.
 *
 * Two things are shown, and they are not the same thing. The *shape* is fixed:
 * every node the backend could execute, in the order the graph declares them.
 * The *path* is what actually happened on this run — which nodes executed,
 * which branch each fork took, and why. A straight-line pipeline makes those
 * look identical; a graph with a fallback edge does not, and the difference is
 * exactly the part worth showing. A run that skipped the reasoning loop because
 * no evidence source was reachable produced a real answer from the rule engine,
 * and nothing else on the page says so.
 *
 * The shape comes from `/api/agent/graph`, served by whichever backend the
 * request header selected, so the picture is the topology the graph is compiled
 * from rather than a diagram maintained separately beside it.
 */

const KIND_TONE = {
  llm: { fill: 'var(--accent-soft)', stroke: 'var(--accent)' },
  deterministic: { fill: 'var(--surface-2)', stroke: 'var(--text-3)' },
  io: { fill: 'var(--surface-2)', stroke: 'var(--text-3)' },
  guard: { fill: 'var(--surface-2)', stroke: 'var(--ok)' },
  fallback: { fill: 'var(--surface-2)', stroke: 'var(--warn)' },
  terminal: { fill: 'var(--surface-2)', stroke: 'var(--text-3)' },
};

const KIND_LABEL = {
  llm: 'LLM call', deterministic: 'deterministic', io: 'data source',
  guard: 'verification', fallback: 'fallback', terminal: 'assembly',
};

export default function AgentGraph({ turn }) {
  const live = useInvestigation();
  const { graph, stages, trace, answer, result, status } = turn || live;
  const [shape, setShape] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [failed, setFailed] = useState(null);

  // The live stream sends the topology at the start of a LangGraph run. The
  // deterministic backend does not, and a run reopened from history has only
  // the path, so the shape is fetched in both of those cases.
  const streamed = graph && Array.isArray(graph.nodes) ? graph : null;

  useEffect(() => {
    if (streamed) { setShape(streamed); return; }
    let cancelled = false;
    getAgentGraph()
      .then((data) => { if (!cancelled) { setShape(data); setFailed(null); } })
      .catch((err) => { if (!cancelled) setFailed(err.message); });
    return () => { cancelled = true; };
  }, [streamed]);

  const nodes = shape?.nodes || [];

  // Which nodes have finished. Derived from the stages that have arrived rather
  // than from a separate progress event, so the drawing cannot claim a node
  // completed that produced no output.
  const done = useMemo(() => {
    const reached = new Set();
    for (const node of nodes) {
      if (!node.emits) continue;
      if (node.emits === 'reasoning') { if (trace.length) reached.add(node.id); continue; }
      if (node.emits === 'answer') { if (answer) reached.add(node.id); continue; }
      if (node.emits === 'result') { if (result) reached.add(node.id); continue; }
      if (stages[node.emits]) reached.add(node.id);
    }
    // The rule-answer node emits nothing of its own; it ran if the stored path
    // says so, or if the answer arrived carrying the loop's failure.
    const path = graph?.path || result?.graph_path;
    if (path) path.forEach((id) => reached.add(id));
    return reached;
  }, [nodes, stages, trace.length, answer, result, graph]);

  const decisions = graph?.decisions || result?.graph_decisions || [];

  // A node the run skipped: on the drawn shape but never reached, once the run
  // is over. Mid-run everything unreached is simply pending.
  const finished = status === 'complete' || status === 'error';

  if (failed && !nodes.length) return null;
  if (!nodes.length) return null;

  return (
    <>
      <div className="li-graph-card glass-panel">
        <button type="button" className="li-graph-preview" onClick={() => setExpanded(true)}
          title="Open the full workflow">
          <div className="li-graph-preview-head">
            <span className="li-graph-title">Agent workflow</span>
            <span className="li-graph-engine">{shape.engine === 'langgraph' ? 'LangGraph' : 'Pipeline'}</span>
            <span className="li-spacer" />
            <span className="li-muted">
              {done.size}/{nodes.length} nodes
              {decisions.length > 0 && ` · ${decisions.length} branch${decisions.length > 1 ? 'es' : ''}`}
            </span>
            <ExpandIcon />
          </div>
          <MiniMap nodes={nodes} edges={shape.edges || []} done={done} />
        </button>
      </div>

      {expanded && (
        <GraphModal shape={shape} done={done} decisions={decisions} finished={finished}
          onClose={() => setExpanded(false)} />
      )}
    </>
  );
}

/** The preview: node dots on their edges, no labels. Legible at 44px tall. */
function MiniMap({ nodes, edges, done }) {
  const W = 320, H = 52, pad = 12;
  const placed = layout(nodes, W, H, pad);
  const byId = Object.fromEntries(placed.map((n) => [n.id, n]));

  // Two layers, because one cannot do both jobs. The edges are stretched to
  // whatever width the card happens to be, which needs `preserveAspectRatio:
  // none`; run that over the nodes as well and every dot becomes a flattened
  // ellipse. The nodes are therefore drawn in a second, unscaled SVG and
  // positioned by percentage, so they stay round at any card width.
  const pct = (v, total) => `${(v / total) * 100}%`;

  return (
    <div className="li-graph-mini">
      <svg className="li-graph-mini-edges" viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none" aria-hidden="true">
        {edges.filter((e) => byId[e.from] && byId[e.to]).map((e, i) => (
          <line key={i} x1={byId[e.from].x} y1={byId[e.from].y}
            x2={byId[e.to].x} y2={byId[e.to].y}
            stroke="var(--border-strong)" strokeWidth="1"
            strokeDasharray={e.conditional ? '3 3' : undefined} />
        ))}
      </svg>
      <svg className="li-graph-mini-nodes" role="img" aria-label="Agent workflow preview">
        {placed.map((n) => {
          const tone = KIND_TONE[n.kind] || KIND_TONE.deterministic;
          const complete = done.has(n.id);
          return (
            <circle key={n.id} cx={pct(n.x, W)} cy={pct(n.y, H)} r={complete ? 5 : 3.5}
              fill={complete ? tone.stroke : 'var(--surface)'}
              stroke={tone.stroke} strokeWidth="1.5"
              opacity={complete ? 1 : 0.5} />
          );
        })}
      </svg>
    </div>
  );
}

/**
 * Column-per-row placement.
 *
 * `row` is the stage index the backend assigns; two nodes sharing one (the
 * reasoning loop and its fallback) sit side by side, which is what makes the
 * branch visible as a fork rather than as a longer line.
 */
function layout(nodes, width, height, pad) {
  const rows = [...new Set(nodes.map((n) => n.row))].sort((a, b) => a - b);
  const stepX = (width - pad * 2) / Math.max(rows.length - 1, 1);
  return nodes.map((n) => {
    const siblings = nodes.filter((m) => m.row === n.row);
    const idx = siblings.indexOf(n);
    const spread = siblings.length > 1 ? (idx - (siblings.length - 1) / 2) : 0;
    return {
      ...n,
      x: pad + rows.indexOf(n.row) * stepX,
      y: height / 2 + spread * (height / 3),
    };
  });
}

/** The maximized view: labelled nodes, the path taken, and why each fork forked. */
function GraphModal({ shape, done, decisions, finished, onClose }) {
  const [selected, setSelected] = useState(null);
  const nodes = shape.nodes || [];
  const edges = shape.edges || [];

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // An edge is "taken" when both ends ran and, for a fork, when a recorded
  // decision names it. Without the second test both sides of every fork would
  // light up on any run that reached the fallback.
  const takenEdges = useMemo(() => {
    const chosen = new Set(decisions.map((d) => `${d.from}->${d.to}`));
    return new Set(edges
      .filter((e) => {
        const ends = (e.from === '__start__' || done.has(e.from))
          && (e.to === '__end__' || done.has(e.to));
        if (!ends) return false;
        if (e.conditional) return chosen.has(`${e.from}->${e.to}`);
        return true;
      })
      .map((e) => `${e.from}->${e.to}`));
  }, [edges, done, decisions]);

  const node = nodes.find((n) => n.id === selected);

  return (
    <div className="li-graph-overlay" role="dialog" aria-modal="true"
      aria-label="Agent workflow" onClick={onClose}>
      <div className="li-graph-modal glass-panel" onClick={(e) => e.stopPropagation()}>
        <header className="li-graph-modal-head">
          <span className="li-graph-title">Agent workflow</span>
          <span className="li-graph-engine">
            {shape.engine === 'langgraph' ? 'LangGraph state machine' : 'Deterministic pipeline'}
          </span>
          <span className="li-spacer" />
          <button type="button" className="li-graph-close" onClick={onClose} aria-label="Close">✕</button>
        </header>

        <div className="li-graph-body">
          <div className="li-graph-flow">
            {nodes.map((n, i) => {
              const complete = done.has(n.id);
              const tone = KIND_TONE[n.kind] || KIND_TONE.deterministic;
              const skipped = finished && !complete;
              const outgoing = edges.filter((e) => e.from === n.id);
              return (
                <div key={n.id} className="li-graph-step">
                  <button type="button"
                    className={`li-graph-node${complete ? ' li-graph-node--done' : ''}`
                      + `${skipped ? ' li-graph-node--skipped' : ''}`
                      + `${selected === n.id ? ' li-graph-node--selected' : ''}`}
                    style={{ borderColor: complete ? tone.stroke : undefined,
                      background: complete ? tone.fill : undefined }}
                    onClick={() => setSelected(selected === n.id ? null : n.id)}>
                    <span className="li-graph-node-index">{i + 1}</span>
                    <span className="li-graph-node-label">{n.label}</span>
                    <span className="li-graph-node-kind">{KIND_LABEL[n.kind] || n.kind}</span>
                    <span className={`li-graph-node-state${complete ? ' is-done' : ''}`}>
                      {complete ? 'ran' : skipped ? 'skipped' : 'pending'}
                    </span>
                  </button>

                  {outgoing.filter((e) => e.to !== '__end__').map((e, k) => {
                    const taken = takenEdges.has(`${e.from}->${e.to}`);
                    const why = decisions.find((d) => d.from === e.from && d.to === e.to)?.why;
                    return (
                      <div key={k}
                        className={`li-graph-edge${e.conditional ? ' li-graph-edge--branch' : ''}`
                          + `${taken ? ' li-graph-edge--taken' : ''}`}>
                        <span className="li-graph-arrow">↓</span>
                        <span className="li-graph-edge-to">{labelOf(nodes, e.to)}</span>
                        {/* A fork is only readable if the side that was taken
                            says so in words. Colour alone leaves the reader
                            comparing two greys to work out which way the run
                            went — and on a branch this is the whole point. */}
                        {/* Only a fork whose source has actually run has been
                            decided. Labelling an unreached branch "not taken"
                            mid-run states an outcome that has not happened
                            yet — every branch below the current node would
                            read as rejected. */}
                        {e.conditional && done.has(e.from) && (
                          <span className={`li-graph-edge-tag${taken ? ' is-taken' : ''}`}>
                            {taken ? 'taken' : 'not taken'}
                          </span>
                        )}
                        {e.conditional && (
                          <span className="li-graph-edge-when">
                            {why || e.when || 'conditional'}
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>

          <aside className="li-graph-side">
            {node ? (
              <>
                <h4>{node.label}</h4>
                <p className="li-graph-detail">{node.detail}</p>
                <p className="li-muted">
                  {KIND_LABEL[node.kind] || node.kind}
                  {' · '}
                  {done.has(node.id) ? 'ran on this investigation'
                    : finished ? 'was not reached' : 'has not run yet'}
                </p>
              </>
            ) : (
              <>
                <h4>How this answer was reached</h4>
                {decisions.length === 0 ? (
                  <p className="li-graph-detail">
                    {shape.engine === 'langgraph'
                      ? 'No branch has been decided yet.'
                      : 'This backend runs a fixed sequence — every stage runs, in order, '
                        + 'with no branch to record. Select a node to see what it does.'}
                  </p>
                ) : (
                  <ol className="li-graph-decisions">
                    {decisions.map((d, i) => (
                      <li key={i}>
                        <div className="li-graph-decision-edge">
                          {labelOf(nodes, d.from)} <span className="li-muted">→</span>{' '}
                          {labelOf(nodes, d.to)}
                        </div>
                        <div className="li-graph-detail">{d.why}</div>
                      </li>
                    ))}
                  </ol>
                )}
                <p className="li-muted" style={{ marginTop: 16 }}>
                  Select any node for what it does.
                </p>
              </>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}

function labelOf(nodes, id) {
  if (id === '__start__') return 'start';
  if (id === '__end__') return 'end';
  return nodes.find((n) => n.id === id)?.label || id;
}

function ExpandIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
    </svg>
  );
}
