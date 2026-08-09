import { useEffect, useRef, useState } from 'react';
import { runInvestigation } from '../api';
import { ErrorBanner } from './common';
import FlowMap, { STAGES } from './FlowMap';
import {
  AnalysisPanel, CandidatesPanel, EvidencePanel, PlanPanel, ReportPanel, SignalsPanel,
  VerifiedPanel, WindowsPanel,
} from './panels';

const PANELS = {
  plan: PlanPanel,
  windows: WindowsPanel,
  evidence: EvidencePanel,
  signals: SignalsPanel,
  candidates: CandidatesPanel,
  analysis: AnalysisPanel,
  verified: VerifiedPanel,
  result: ReportPanel,
};

export default function InvestigationResults({ request }) {
  const [stagesData, setStagesData] = useState({});
  const [currentStageId, setCurrentStageId] = useState(null);
  const [selectedStageId, setSelectedStageId] = useState(null);
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
            setStagesData((prev) => ({ ...prev, [event.stage]: event.data }));
            setCurrentStageId(event.stage);
            setSelectedStageId((prevSelected) =>
              // Follow the stream unless the user has already clicked a
              // different completed node to look at.
              prevSelected === null || prevSelected === event.stage ? event.stage : prevSelected
            );
          },
        });
        if (mounted) {
          setStatus((s) => (s === 'error' ? s : 'complete'));
          setCurrentStageId(null);
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

  const Panel = selectedStageId ? PANELS[selectedStageId] : null;
  const finalAnalysis = stagesData.result?.analysis;
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

      <div className="glass-panel li-flowmap-panel">
        <FlowMap
          stagesData={stagesData}
          currentStageId={status === 'streaming' ? currentStageId : null}
          selectedStageId={selectedStageId}
          onSelect={setSelectedStageId}
        />
      </div>

      <div className="glass-panel li-detail-panel">
        {!selectedStageId ? (
          <div className="li-waiting">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 002-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
            </svg>
            <p>Waiting for the agent to begin…</p>
          </div>
        ) : (
          <div className="animate-fade-in">
            <div className="li-detail-head">
              <h2>{STAGES.find((s) => s.id === selectedStageId)?.label}</h2>
              <span className="li-detail-hint">{STAGES.find((s) => s.id === selectedStageId)?.hint}</span>
            </div>
            {Panel && (
              <Panel
                data={stagesData[selectedStageId]}
                chosenId={finalAnalysis?.chosen_candidate_id}
                engineTopId={finalAnalysis?.engine_top_candidate_id}
              />
            )}
          </div>
        )}
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
