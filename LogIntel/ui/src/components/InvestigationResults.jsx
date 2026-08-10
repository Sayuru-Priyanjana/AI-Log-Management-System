import { useEffect, useRef, useState } from 'react';
import { runInvestigation } from '../api';
import { ErrorBanner } from './common';
import ReactMarkdown from 'react-markdown';

export default function InvestigationResults({ request }) {
  const [events, setEvents] = useState([]);
  const [status, setStatus] = useState('connecting');
  const [errorMsg, setErrorMsg] = useState(null);
  const startedAt = useRef(Date.now());
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let mounted = true;
    startedAt.current = Date.now();

    const tick = setInterval(() => {
      if (mounted && status !== 'complete' && status !== 'error') {
        setElapsed(Date.now() - startedAt.current);
      }
    }, 250);

    (async () => {
      try {
        setStatus('connecting');
        await runInvestigation(request, {
          signal: controller.signal,
          onEvent: (event) => {
            if (!mounted) return;
            if (event.stage === 'error') {
              setStatus('error');
              setErrorMsg(event.data?.detail || 'The investigation failed.');
              return;
            }
            setStatus('streaming');
            setEvents((prev) => [...prev, event]);
          },
        });
        if (mounted) {
          setStatus((s) => (s === 'error' ? s : 'complete'));
        }
      } catch (err) {
        if (mounted && err.name !== 'AbortError') {
          setStatus('error');
          setErrorMsg(err.message);
        }
      }
    })();

    return () => {
      mounted = false;
      clearInterval(tick);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request]);

  const seconds = (elapsed / 1000).toFixed(1);

  return (
    <div className="li-results">
      <div className="glass-panel li-results-header">
        <div>
          <h3>
            Investigating <span className="text-gradient">{request.system_id}</span>
            {request.service_hint ? <span className="li-muted"> · {request.service_hint}</span> : null}
          </h3>
          <p className="li-muted">{request.question}</p>
        </div>
        <div className="li-status-pill">
          {status === 'connecting' && <StatusDot color="var(--warning)" label="Connecting…" pulse />}
          {status === 'streaming' && <StatusDot color="var(--accent-color)" label={`Working… ${seconds}s`} pulse />}
          {status === 'complete' && <StatusDot color="var(--success)" label={`Done in ${seconds}s`} />}
          {status === 'error' && <StatusDot color="var(--error)" label="Failed" />}
        </div>
      </div>

      {errorMsg && (
        <ErrorBanner><strong>Something went wrong.</strong> {errorMsg}</ErrorBanner>
      )}

      <div className="glass-panel" style={{ padding: '2rem', display: 'flex', flexDirection: 'column', gap: '1rem', width: '100%', maxWidth: '800px', margin: '0 auto' }}>
        {events.length === 0 && status !== 'error' && (
          <div className="li-waiting" style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
             <p>Waiting for the agent to begin…</p>
          </div>
        )}
        
        {events.map((evt, idx) => (
          <EventBlock key={idx} event={evt} />
        ))}
        
        {status === 'streaming' && (
           <div style={{ padding: '1rem', color: 'var(--text-muted)' }} className="animate-pulse-glow">
             Agent is thinking...
           </div>
        )}
      </div>
    </div>
  );
}

function EventBlock({ event }) {
  const { stage, data } = event;
  
  let content = null;
  let title = stage.toUpperCase();
  let color = 'var(--text-color)';
  let bg = 'var(--bg-panel)';

  if (stage === 'plan') {
    title = 'Investigation Plan';
    content = <div>Goal: {data.goal} (Intent: {data.intent})</div>;
  } else if (stage === 'windows') {
    title = 'Time Windows';
    content = <div>Incident: {data.incident?.start}</div>;
  } else if (stage === 'evidence') {
    title = 'Initial Evidence Collected';
    content = <div>Logs: {data.logs?.documents}, Events: {data.events?.count}</div>;
  } else if (stage === 'thought') {
    title = `Agent Thought (Step ${data.step})`;
    color = 'var(--accent-color)';
    bg = 'rgba(74, 144, 226, 0.1)';
    content = <div style={{ fontStyle: 'italic' }}>"{data.text}"</div>;
  } else if (stage === 'action') {
    title = `Action: ${data.tool}`;
    color = 'var(--warning)';
    bg = 'rgba(245, 166, 35, 0.1)';
    content = <pre style={{ margin: 0, background: 'transparent' }}>{JSON.stringify(data.input, null, 2)}</pre>;
  } else if (stage === 'observation') {
    title = 'Observation';
    color = 'var(--success)';
    bg = 'rgba(126, 211, 33, 0.1)';
    content = <pre style={{ margin: 0, background: 'transparent', whiteSpace: 'pre-wrap' }}>{data.text}</pre>;
  } else if (stage === 'analysis') {
    title = 'Conclusion';
    content = <div><ReactMarkdown>{data.cause}</ReactMarkdown></div>;
  } else if (stage === 'verified' || stage === 'result' || stage === 'candidates' || stage === 'signals') {
    return null; // hide these intermediate/legacy stages
  }

  return (
    <div style={{
      border: `1px solid ${color}`,
      background: bg,
      borderRadius: '8px',
      padding: '1rem',
      animation: 'fadeIn 0.3s ease-out'
    }}>
      <div style={{ color, fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        {title}
      </div>
      <div style={{ fontSize: '0.95rem', color: 'var(--text-color)', lineHeight: 1.5 }}>
        {content}
      </div>
    </div>
  );
}

function StatusDot({ color, label, pulse }) {
  return (
    <>
      <div
        className={pulse ? 'animate-pulse-glow' : ''}
        style={{ width: 9, height: 9, borderRadius: '50%', background: color, flexShrink: 0 }}
      />
      <span style={{ color, fontWeight: 550 }}>{label}</span>
    </>
  );
}
