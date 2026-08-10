import { STAGES } from './FlowMap';

export function PlanPanel({ data }) {
  if (!data) return null;
  return (
    <div className="li-panel-content">
      <p><strong>Intent:</strong> {data.intent}</p>
      <p><strong>Goal:</strong> {data.goal}</p>
    </div>
  );
}

export function WindowsPanel({ data }) {
  if (!data) return null;
  return (
    <div className="li-panel-content">
      <p><strong>Incident Window:</strong> {data.incident?.start} to {data.incident?.end}</p>
    </div>
  );
}

export function EvidencePanel({ data }) {
  if (!data) return null;
  return (
    <div className="li-panel-content">
      <p>Collected evidence across logs, events, and metrics.</p>
      <pre className="li-code">{JSON.stringify(data, null, 2)}</pre>
    </div>
  );
}

export function ReportPanel({ data }) {
  if (!data) return null;
  return (
    <div className="li-panel-content">
      <h3>Conclusion</h3>
      <p>{data.analysis?.cause}</p>
    </div>
  );
}
