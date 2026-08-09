import { useState } from 'react'
import './index.css'

interface TimeRange {
  type: "relative" | "absolute";
  start?: string;
  end?: string;
  duration?: string;
}

interface InvestigationPlan {
  intent: string;
  system_id: string;
  environment: string;
  service: string | null;
  time_range: TimeRange;
  required_data: string[];
  investigation_goal: string;
}

interface ApplicationLogEvidence {
  timestamp: string;
  level: string | null;
  message: string;
  service_name: string | null;
}

interface KubernetesEventEvidence {
  timestamp: string;
  action: string | null;
  reason: string | null;
  message: string;
  pod_name: string | null;
}

interface MetricSummary {
  average?: number;
  maximum?: number;
  minimum?: number;
  initial?: number;
  final?: number;
  increase?: number;
}

interface MetricEvidence {
  metric_name: string;
  metric_type: string;
  unit: string;
  status: string;
  reason?: string;
  labels: Record<string, string>;
  summary?: MetricSummary;
}

interface InvestigationEvidence {
  application_logs: ApplicationLogEvidence[];
  kubernetes_events: KubernetesEventEvidence[];
  metrics: MetricEvidence[];
  status: Record<string, string>;
  queries: Record<string, any>;
}

interface TimelineEvidence {
  id: string;
  timestamp: string;
  source_type: "application_log" | "kubernetes_event" | "metric";
  system_id: string;
  environment: string;
  service_name?: string;
  severity?: string;
  title: string;
  message?: string;
  metric_name?: string;
  metric_value?: number;
  metric_unit?: string;
}

interface OperationalSignal {
  type: string;
  severity: string;
  timestamp: string;
  service?: string;
  pod?: string;
  count?: number;
  increase?: number;
}

interface CorrelationGroup {
  id: string;
  start_time: string;
  end_time: string;
  evidence_ids: string[];
  signals: string[];
  summary?: string;
}

interface CorrelatedEvidence {
  timeline: TimelineEvidence[];
  relationships: any[];
  groups: CorrelationGroup[];
  signals: OperationalSignal[];
}

interface CauseHypothesis {
  description: string;
  confidence: number;
  evidence_ids: string[];
  reasoning: string;
}

interface ContributingFactor {
  factor: string;
  confidence: number;
  evidence_ids: string[];
  explanation: string;
}

interface InvestigationAnalysis {
  incident_detected: boolean;
  severity: "low" | "medium" | "high" | "critical" | "unknown";
  summary: string;
  incident_timeline: string[];
  likely_causes: CauseHypothesis[];
  contributing_factors: ContributingFactor[];
  supporting_evidence: string[];
  conflicting_evidence: string[];
  missing_evidence: string[];
  recommended_next_steps: string[];
  overall_confidence: number;
}

const SYSTEMS = [
  { id: "ecommerce-platform", name: "E-Commerce Platform" },
  { id: "payment-platform", name: "Payment Platform" },
  { id: "inventory-platform", name: "Inventory Platform" }
]

const ENVIRONMENTS = ["production", "staging", "development"]

function App() {
  const [systemId, setSystemId] = useState(SYSTEMS[0].id)
  const [environment, setEnvironment] = useState(ENVIRONMENTS[0])
  const [question, setQuestion] = useState("")
  
  const [plan, setPlan] = useState<InvestigationPlan | null>(null)
  const [evidence, setEvidence] = useState<InvestigationEvidence | null>(null)
  const [correlation, setCorrelation] = useState<CorrelatedEvidence | null>(null)
  const [analysis, setAnalysis] = useState<InvestigationAnalysis | null>(null)
  const [rawPrompt, setRawPrompt] = useState<string | null>(null)
  
  const [isInvestigating, setIsInvestigating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  
  const [activeNode, setActiveNode] = useState<string>("user")
  const [showPromptModal, setShowPromptModal] = useState(false)

  const formatMetricValue = (val: number | undefined | null, unit: string) => {
    if (val === undefined || val === null) return "N/A"
    if (unit === "bytes") {
      if (val > 1024 * 1024 * 1024) return `${(val / (1024 * 1024 * 1024)).toFixed(2)} GB`
      if (val > 1024 * 1024) return `${(val / (1024 * 1024)).toFixed(2)} MB`
      if (val > 1024) return `${(val / 1024).toFixed(2)} KB`
      return `${val.toFixed(0)} B`
    }
    if (unit === "cores") {
      return `${val.toFixed(3)} cores`
    }
    return `${val.toFixed(1)}`
  }

  const handleInvestigate = async () => {
    if (!question.trim()) {
      setError("Please enter a question.")
      return
    }

    setIsInvestigating(true)
    setError(null)
    setPlan(null)
    setEvidence(null)
    setCorrelation(null)
    setAnalysis(null)
    setActiveNode("orchestrator")

    const systemName = SYSTEMS.find(s => s.id === systemId)?.name || systemId

    try {
      const response = await fetch("http://localhost:8000/api/investigations/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          system_id: systemId,
          system_name: systemName,
          environment,
          question
        })
      })

      if (!response.ok) {
        const data = await response.json().catch(() => null)
        throw new Error(data?.detail || `HTTP error! status: ${response.status}`)
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder("utf-8");
      
      if (reader) {
        let buffer = "";
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          
          buffer += decoder.decode(value, { stream: true });
          let boundary = buffer.indexOf('\n');
          while (boundary !== -1) {
            const line = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 1);
            
            if (line.trim()) {
                const payload = JSON.parse(line);
                if (payload.step === 'error') {
                    setError(payload.message);
                    setIsInvestigating(false);
                    return;
                } else if (payload.step === 'orchestrator') {
                    setPlan(payload.data);
                    setActiveNode("orchestrator");
                } else if (payload.step === 'dispatcher') {
                    setEvidence(payload.data);
                    setActiveNode("evidence_agent");
                } else if (payload.step === 'correlation') {
                    setCorrelation(payload.data);
                    setActiveNode("correlation");
                } else if (payload.step === 'analysis') {
                    setAnalysis(payload.data);
                    setRawPrompt(payload.prompt);
                    setActiveNode("analysis");
                }
            }
            boundary = buffer.indexOf('\n');
          }
        }
      }

    } catch (err: any) {
      if (err.message === "Failed to fetch") {
        setError("Unable to contact the local AI model or API backend.")
      } else {
        setError(err.message || "An unexpected error occurred.")
      }
    } finally {
      setIsInvestigating(false)
    }
  }

  // --- Node Rendering Helper ---
  const renderNode = (id: string, label: string, colorClass: string, isAvailable: boolean = true) => {
    const isActive = activeNode === id
    return (
      <button
        onClick={() => setActiveNode(id)}
        disabled={!isAvailable && id !== 'user'}
        className={`relative flex items-center justify-center px-4 py-3 text-sm font-bold rounded-lg border-2 transition-all duration-300 w-48 z-10 
          ${isActive ? 'ring-4 ring-white shadow-[0_0_20px_rgba(255,255,255,0.3)] scale-110' : 'hover:scale-105'}
          ${!isAvailable && id !== 'user' ? 'opacity-30 cursor-not-allowed grayscale' : 'shadow-xl cursor-pointer'}
          ${colorClass}
        `}
      >
        {label}
        {isInvestigating && isActive && (
           <span className="absolute -top-2 -right-2 flex h-4 w-4">
             <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-white opacity-75"></span>
             <span className="relative inline-flex rounded-full h-4 w-4 bg-white"></span>
           </span>
        )}
      </button>
    )
  }

  // --- Data Inspector Renderer ---
  const renderDataInspector = () => {
    if (isInvestigating && !plan && activeNode === 'orchestrator') {
      return (
        <div className="h-full flex flex-col items-center justify-center text-slate-400 space-y-4">
          <div className="w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full animate-spin"></div>
          <p className="animate-pulse">Analyzing system state...</p>
        </div>
      )
    }

    switch (activeNode) {
      case 'user':
        return (
          <div className="space-y-4">
            <h3 className="text-xl font-bold text-white mb-4 flex items-center gap-2">
              <span className="bg-slate-700 w-8 h-8 rounded-full flex items-center justify-center">👤</span>
              User Input
            </h3>
            <div className="bg-slate-800 p-4 rounded-xl border border-slate-700">
              <label className="text-xs text-slate-400 uppercase font-bold tracking-wider">Prompt</label>
              <p className="text-slate-200 mt-1 text-lg">"{question || "No prompt provided."}"</p>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="bg-slate-800 p-4 rounded-xl border border-slate-700">
                <label className="text-xs text-slate-400 uppercase font-bold tracking-wider">Target System</label>
                <p className="text-slate-200 mt-1">{SYSTEMS.find(s => s.id === systemId)?.name}</p>
              </div>
              <div className="bg-slate-800 p-4 rounded-xl border border-slate-700">
                <label className="text-xs text-slate-400 uppercase font-bold tracking-wider">Environment</label>
                <p className="text-slate-200 mt-1 capitalize">{environment}</p>
              </div>
            </div>
          </div>
        )

      case 'orchestrator':
        return (
          <div className="space-y-4">
            <h3 className="text-xl font-bold text-emerald-400 mb-4 flex items-center gap-2">
              <span className="bg-emerald-900/50 w-8 h-8 rounded-full flex items-center justify-center">🧠</span>
              Orchestrator Agent Output
            </h3>
            {!plan ? <p className="text-slate-500 italic">No investigation plan generated yet.</p> : (
              <>
                <div className="bg-emerald-900/20 p-4 rounded-xl border border-emerald-800/50">
                  <label className="text-xs text-emerald-500 uppercase font-bold tracking-wider">Detected Intent</label>
                  <p className="text-emerald-100 mt-1">{plan.intent}</p>
                </div>
                <div className="bg-slate-800 p-4 rounded-xl border border-slate-700">
                  <label className="text-xs text-slate-400 uppercase font-bold tracking-wider">Time Range</label>
                  <p className="text-slate-200 mt-1 font-mono">
                    {plan.time_range.type === 'relative' ? plan.time_range.duration : `${plan.time_range.start} - ${plan.time_range.end}`}
                  </p>
                </div>
                <div className="bg-slate-800 p-4 rounded-xl border border-slate-700">
                  <label className="text-xs text-slate-400 uppercase font-bold tracking-wider">Target Service</label>
                  <p className="text-slate-200 mt-1">{plan.service || 'Global'}</p>
                </div>
              </>
            )}
          </div>
        )

      case 'dispatcher':
        return (
          <div className="space-y-4">
            <h3 className="text-xl font-bold text-slate-200 mb-4 flex items-center gap-2">
              <span className="bg-slate-700 w-8 h-8 rounded-full flex items-center justify-center">⚙️</span>
              Dispatcher Activity
            </h3>
            <p className="text-slate-400">The dispatcher routes the investigation plan to the specialized tools.</p>
            {plan && (
              <div className="bg-slate-800 p-4 rounded-xl border border-slate-700">
                <label className="text-xs text-slate-400 uppercase font-bold tracking-wider">Tools Activated</label>
                <div className="flex flex-wrap gap-2 mt-2">
                  <span className="px-3 py-1 bg-pink-900/30 text-pink-300 border border-pink-800 rounded-full text-xs">Log Search</span>
                  <span className="px-3 py-1 bg-pink-900/30 text-pink-300 border border-pink-800 rounded-full text-xs">Event Search</span>
                  <span className="px-3 py-1 bg-pink-900/30 text-pink-300 border border-pink-800 rounded-full text-xs">Metrics Tool</span>
                </div>
              </div>
            )}
          </div>
        )

      case 'tool_log':
        return (
          <div className="space-y-4 h-full flex flex-col">
            <h3 className="text-xl font-bold text-pink-400 mb-4 flex items-center gap-2 shrink-0">
              <span className="bg-pink-900/50 w-8 h-8 rounded-full flex items-center justify-center">📄</span>
              Log Search Output
            </h3>
            {!evidence ? <p className="text-slate-500 italic">No logs collected.</p> : (
              <div className="space-y-3 flex-1 overflow-y-auto pr-2 custom-scrollbar">
                <div className="bg-slate-800 p-3 rounded-lg border border-slate-700 flex justify-between items-center sticky top-0 z-10 shadow-md">
                  <span className="text-slate-300">Total Logs Retrieved:</span>
                  <span className="text-white font-bold bg-pink-600 px-2 py-1 rounded-md">{evidence.application_logs.length}</span>
                </div>
                {evidence.application_logs.slice(0, 50).map((log, i) => (
                  <div key={i} className="bg-slate-900 p-3 rounded border border-slate-800 text-sm">
                    <div className="flex justify-between items-start mb-2">
                      <span className="text-xs text-slate-500 font-mono">{log.timestamp}</span>
                      <span className={`text-[10px] px-2 py-0.5 rounded font-bold uppercase ${log.level === 'ERROR' ? 'bg-red-900/50 text-red-400' : 'bg-slate-800 text-slate-300'}`}>{log.level || 'INFO'}</span>
                    </div>
                    <p className="text-slate-300 font-mono text-xs break-all">{log.message}</p>
                  </div>
                ))}
                {evidence.application_logs.length > 50 && <p className="text-xs text-center text-slate-500 py-2">...and {evidence.application_logs.length - 50} more items hidden.</p>}
              </div>
            )}
          </div>
        )

      case 'tool_event':
        return (
          <div className="space-y-4 h-full flex flex-col">
            <h3 className="text-xl font-bold text-pink-400 mb-4 flex items-center gap-2 shrink-0">
              <span className="bg-pink-900/50 w-8 h-8 rounded-full flex items-center justify-center">⚡</span>
              Kubernetes Events Output
            </h3>
            {!evidence ? <p className="text-slate-500 italic">No events collected.</p> : (
              <div className="space-y-3 flex-1 overflow-y-auto pr-2 custom-scrollbar">
                <div className="bg-slate-800 p-3 rounded-lg border border-slate-700 flex justify-between items-center sticky top-0 z-10 shadow-md">
                  <span className="text-slate-300">Total Events Retrieved:</span>
                  <span className="text-white font-bold bg-pink-600 px-2 py-1 rounded-md">{evidence.kubernetes_events.length}</span>
                </div>
                {evidence.kubernetes_events.map((ev, i) => (
                  <div key={i} className="bg-slate-900 p-3 rounded border border-slate-800 text-sm">
                    <div className="flex justify-between mb-1">
                      <span className="text-xs font-bold text-indigo-400">{ev.reason}</span>
                      <span className="text-xs text-slate-500">{ev.pod_name}</span>
                    </div>
                    <p className="text-slate-300 text-xs">{ev.message}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        )

      case 'tool_metrics':
        return (
          <div className="space-y-4 h-full flex flex-col">
            <h3 className="text-xl font-bold text-pink-400 mb-4 flex items-center gap-2 shrink-0">
              <span className="bg-pink-900/50 w-8 h-8 rounded-full flex items-center justify-center">📈</span>
              Metrics Tool Output
            </h3>
            {!evidence ? <p className="text-slate-500 italic">No metrics collected.</p> : (
              <div className="space-y-4 flex-1 overflow-y-auto pr-2 custom-scrollbar">
                {evidence.metrics.map((m, i) => (
                  <div key={i} className="bg-slate-900 p-4 rounded-xl border border-slate-800">
                    <div className="flex justify-between items-center mb-3">
                      <h4 className="font-bold text-slate-200">{m.metric_name}</h4>
                      <span className={`text-[10px] px-2 py-1 rounded uppercase font-bold ${m.status === 'success' ? 'bg-emerald-900/30 text-emerald-400' : 'bg-red-900/30 text-red-400'}`}>{m.status}</span>
                    </div>
                    {m.summary && (
                      <div className="grid grid-cols-2 gap-2 text-xs">
                        {m.summary.average !== undefined && <div className="bg-slate-800 p-2 rounded">Avg: {formatMetricValue(m.summary.average, m.unit)}</div>}
                        {m.summary.maximum !== undefined && <div className="bg-slate-800 p-2 rounded">Max: {formatMetricValue(m.summary.maximum, m.unit)}</div>}
                        {m.summary.increase !== undefined && <div className="bg-slate-800 p-2 rounded text-amber-400">Inc: +{formatMetricValue(m.summary.increase, m.unit)}</div>}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )

      case 'evidence_agent':
        return (
          <div className="space-y-4">
            <h3 className="text-xl font-bold text-emerald-400 mb-4 flex items-center gap-2">
              <span className="bg-emerald-900/50 w-8 h-8 rounded-full flex items-center justify-center">📦</span>
              Aggregated Evidence
            </h3>
            {!evidence ? <p className="text-slate-500 italic">No evidence aggregated.</p> : (
              <div className="grid grid-cols-1 gap-4">
                <div className="bg-slate-800 p-6 rounded-xl border border-slate-700 flex flex-col items-center justify-center">
                  <span className="text-4xl font-bold text-blue-400">{evidence.application_logs.length}</span>
                  <span className="text-sm text-slate-400 mt-2 uppercase tracking-wider">Log Entries</span>
                </div>
                <div className="bg-slate-800 p-6 rounded-xl border border-slate-700 flex flex-col items-center justify-center">
                  <span className="text-4xl font-bold text-indigo-400">{evidence.kubernetes_events.length}</span>
                  <span className="text-sm text-slate-400 mt-2 uppercase tracking-wider">K8s Events</span>
                </div>
                <div className="bg-slate-800 p-6 rounded-xl border border-slate-700 flex flex-col items-center justify-center">
                  <span className="text-4xl font-bold text-pink-400">{evidence.metrics.length}</span>
                  <span className="text-sm text-slate-400 mt-2 uppercase tracking-wider">Metric Streams</span>
                </div>
              </div>
            )}
          </div>
        )

      case 'correlation':
        return (
          <div className="space-y-4 h-full flex flex-col">
            <h3 className="text-xl font-bold text-fuchsia-400 mb-4 flex items-center gap-2 shrink-0">
              <span className="bg-fuchsia-900/50 w-8 h-8 rounded-full flex items-center justify-center">🔗</span>
              Correlation Engine
            </h3>
            {!correlation ? <p className="text-slate-500 italic">No correlation performed yet.</p> : (
              <div className="space-y-4 flex-1 overflow-y-auto pr-2 custom-scrollbar">
                <p className="text-slate-300">Phase 3 Deterministic Correlation Engine processed the evidence and identified the following relationships:</p>
                {correlation.groups.map((group, i) => (
                  <div key={i} className="bg-fuchsia-900/10 p-4 rounded-xl border border-fuchsia-800/30">
                    <p className="text-fuchsia-200 text-sm font-medium">{group.summary}</p>
                    <div className="flex justify-between items-center mt-3 text-xs">
                      <span className="text-slate-400">Events: {group.evidence_ids.length}</span>
                      <span className="text-slate-400">Signals: {group.signals.length}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )

      case 'analysis':
        return (
          <div className="space-y-6 h-full flex flex-col">
            <h3 className="text-xl font-bold text-cyan-400 mb-4 flex justify-between items-center">
              <div className="flex items-center gap-2">
                <span className="bg-cyan-900/50 w-8 h-8 rounded-full flex items-center justify-center">🤖</span>
                AI Analysis (Qwen 2.5 Coder)
              </div>
              {rawPrompt && (
                <button 
                  onClick={() => setShowPromptModal(true)}
                  className="px-3 py-1 bg-cyan-900/50 hover:bg-cyan-800/80 text-cyan-300 text-xs font-bold rounded-full transition-colors border border-cyan-700/50"
                >
                  View Raw Prompt
                </button>
              )}
            </h3>
            {!analysis ? <p className="text-slate-500 italic">No analysis available.</p> : (
              <div className="space-y-6 flex-1 overflow-y-auto pr-2 custom-scrollbar">
                
                {/* Summary & Severity */}
                <div className="bg-slate-800 p-5 rounded-xl border border-slate-700">
                  <div className="flex justify-between items-center mb-3">
                    <h4 className="text-slate-300 font-bold uppercase tracking-wider text-xs">Summary</h4>
                    <span className={`text-[10px] px-2 py-1 rounded uppercase font-bold ${
                      analysis.severity === 'critical' ? 'bg-red-900 text-red-100' :
                      analysis.severity === 'high' ? 'bg-orange-900 text-orange-200' :
                      analysis.severity === 'medium' ? 'bg-amber-900 text-amber-200' :
                      'bg-slate-700 text-slate-300'
                    }`}>Severity: {analysis.severity}</span>
                  </div>
                  <p className="text-slate-200 text-sm leading-relaxed">{analysis.summary}</p>
                </div>

                {/* Likely Causes */}
                <div>
                  <h4 className="text-slate-400 font-bold uppercase tracking-wider text-xs mb-3">AI Inference: Likely Causes</h4>
                  <div className="space-y-3">
                    {analysis.likely_causes.map((cause, i) => (
                      <div key={i} className="bg-cyan-900/10 p-4 rounded-xl border border-cyan-800/30">
                        <div className="flex justify-between items-start mb-2">
                          <p className="text-cyan-300 font-bold text-sm">{cause.description}</p>
                          <span className="text-cyan-400 text-xs bg-cyan-900/50 px-2 py-1 rounded">{(cause.confidence * 100).toFixed(0)}% Conf</span>
                        </div>
                        <p className="text-slate-300 text-xs mt-2 italic">{cause.reasoning}</p>
                        <div className="mt-3 flex flex-wrap gap-2">
                          {cause.evidence_ids.map(id => (
                            <span key={id} className="text-[10px] font-mono bg-slate-800 text-slate-400 px-1.5 py-0.5 rounded border border-slate-700 cursor-pointer hover:bg-slate-700">{id}</span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Contributing Factors */}
                {analysis.contributing_factors.length > 0 && (
                  <div>
                    <h4 className="text-slate-400 font-bold uppercase tracking-wider text-xs mb-3">Contributing Factors</h4>
                    <div className="space-y-3">
                      {analysis.contributing_factors.map((factor, i) => (
                        <div key={i} className="bg-slate-800/50 p-4 rounded-xl border border-slate-700">
                          <div className="flex justify-between items-start mb-2">
                            <p className="text-slate-200 font-bold text-sm">{factor.factor}</p>
                            <span className="text-slate-400 text-xs bg-slate-800 px-2 py-1 rounded">{(factor.confidence * 100).toFixed(0)}% Conf</span>
                          </div>
                          <p className="text-slate-400 text-xs mt-2">{factor.explanation}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Timeline Inference */}
                {analysis.incident_timeline.length > 0 && (
                  <div>
                    <h4 className="text-slate-400 font-bold uppercase tracking-wider text-xs mb-3">Incident Timeline</h4>
                    <ul className="list-disc pl-5 space-y-1">
                      {analysis.incident_timeline.map((item, i) => (
                        <li key={i} className="text-slate-300 text-sm">{item}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Missing Evidence & Next Steps */}
                <div className="grid grid-cols-1 gap-4">
                  {analysis.missing_evidence.length > 0 && (
                    <div className="bg-amber-900/10 p-4 rounded-xl border border-amber-800/30">
                      <h4 className="text-amber-500 font-bold uppercase tracking-wider text-[10px] mb-2">Missing Evidence</h4>
                      <ul className="list-disc pl-5 space-y-1">
                        {analysis.missing_evidence.map((item, i) => (
                          <li key={i} className="text-amber-200/80 text-xs">{item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {analysis.recommended_next_steps.length > 0 && (
                    <div className="bg-blue-900/10 p-4 rounded-xl border border-blue-800/30">
                      <h4 className="text-blue-400 font-bold uppercase tracking-wider text-[10px] mb-2">Recommended Next Steps</h4>
                      <ul className="list-disc pl-5 space-y-1">
                        {analysis.recommended_next_steps.map((item, i) => (
                          <li key={i} className="text-blue-200/80 text-xs">{item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>

              </div>
            )}
          </div>
        )

      case 'timeline':
        return (
          <div className="space-y-4 h-full flex flex-col">
            <h3 className="text-xl font-bold text-slate-200 mb-4 shrink-0">Correlated Timeline</h3>
            {!correlation ? <p className="text-slate-500 italic">No timeline available.</p> : (
              <div className="space-y-4 pl-4 border-l-2 border-slate-700 flex-1 overflow-y-auto pr-2 custom-scrollbar">
                {correlation.timeline.map((item, i) => (
                  <div key={i} className="relative">
                    <div className={`absolute -left-[21px] w-3 h-3 rounded-full mt-1 ${item.source_type === 'application_log' ? 'bg-blue-500' : item.source_type === 'kubernetes_event' ? 'bg-emerald-500' : 'bg-pink-500'}`}></div>
                    <div className="bg-slate-900 p-3 rounded-lg border border-slate-800">
                      <div className="flex justify-between items-start mb-1">
                        <span className="text-xs font-mono text-slate-400">{new Date(item.timestamp).toLocaleTimeString()}</span>
                        <span className={`text-[10px] px-1.5 py-0.5 rounded uppercase font-bold ${item.severity === 'ERROR' || item.severity === 'HIGH' || item.severity === 'CRITICAL' ? 'bg-red-900/50 text-red-300' : 'bg-slate-800 text-slate-300'}`}>
                          {item.severity || item.source_type.replace('_', ' ')}
                        </span>
                      </div>
                      <h5 className="text-sm font-bold text-slate-200">{item.title}</h5>
                      {item.service_name && <p className="text-xs text-indigo-400 mt-1">{item.service_name}</p>}
                      {item.message && <p className="text-xs text-slate-400 mt-1 line-clamp-2">{item.message}</p>}
                      {item.metric_value !== undefined && <p className="text-xs font-mono text-slate-300 mt-1">{formatMetricValue(item.metric_value, item.metric_unit || '')}</p>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      
      case 'signals':
        return (
          <div className="space-y-4 h-full flex flex-col">
            <h3 className="text-xl font-bold text-slate-200 mb-4 shrink-0">Operational Signals</h3>
            {!correlation ? <p className="text-slate-500 italic">No signals detected.</p> : (
              <div className="grid grid-cols-1 gap-3 flex-1 overflow-y-auto pr-2 custom-scrollbar">
                {correlation.signals.map((sig, i) => (
                  <div key={i} className={`p-4 rounded-xl border flex items-center gap-4 ${sig.severity === 'high' ? 'bg-red-900/20 border-red-800/50' : sig.severity === 'medium' ? 'bg-amber-900/20 border-amber-800/50' : 'bg-slate-800 border-slate-700'}`}>
                    <div className={`text-2xl ${sig.severity === 'high' ? 'text-red-400' : 'text-amber-400'}`}>⚠</div>
                    <div>
                      <p className="font-bold text-slate-200 capitalize">{sig.type.replace(/_/g, ' ')}</p>
                      <p className="text-xs text-slate-400 mt-1">{new Date(sig.timestamp).toLocaleTimeString()} {sig.service ? `• ${sig.service}` : ''}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      
      case 'relationships':
        return (
          <div className="space-y-4">
            <h3 className="text-xl font-bold text-slate-200 mb-4">Entity Relationships</h3>
            {!correlation ? <p className="text-slate-500 italic">No relationships mapped.</p> : (
              <div className="bg-slate-800 p-6 rounded-xl border border-slate-700 text-center">
                <p className="text-slate-400">Graph mapping of {correlation.relationships.length} entity relationships (Pods, Services, Nodes) derived from the evidence.</p>
              </div>
            )}
          </div>
        )

      default:
        return null
    }
  }

  return (
    <div className="min-h-screen bg-[#0B1120] text-slate-100 font-sans">
      {/* Header */}
      <header className="border-b border-slate-800 bg-slate-900/50 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-[1400px] mx-auto p-4 flex justify-between items-center">
          <div>
            <h1 className="text-2xl font-black bg-gradient-to-r from-blue-400 via-indigo-400 to-fuchsia-400 bg-clip-text text-transparent">
              AI Log Analysis System
            </h1>
            <p className="text-xs font-semibold text-slate-500 tracking-widest uppercase mt-1">Architecture Visualizer Mode</p>
          </div>
        </div>
      </header>

      {/* Main Grid 3-Column Layout */}
      <main className="max-w-[1600px] mx-auto p-6 grid grid-cols-1 lg:grid-cols-12 gap-8 h-[calc(100vh-80px)]">
        
        {/* LEFT COL: Form */}
        <div className="col-span-12 lg:col-span-3 flex flex-col space-y-6">
          <div className="bg-slate-900/80 backdrop-blur p-6 rounded-2xl border border-slate-800 shadow-2xl h-full flex flex-col">
            <h2 className="text-lg font-bold text-white mb-4">Investigation Setup</h2>
            
            <div className="space-y-4 flex-1">
              <div>
                <label className="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1.5">System</label>
                <select 
                  value={systemId} 
                  onChange={(e) => setSystemId(e.target.value)}
                  className="w-full bg-[#0F172A] border border-slate-700 rounded-lg p-2.5 text-sm text-slate-200 focus:ring-2 focus:ring-blue-500 outline-none"
                >
                  {SYSTEMS.map(sys => <option key={sys.id} value={sys.id}>{sys.name}</option>)}
                </select>
              </div>

              <div>
                <label className="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1.5">Environment</label>
                <select 
                  value={environment} 
                  onChange={(e) => setEnvironment(e.target.value)}
                  className="w-full bg-[#0F172A] border border-slate-700 rounded-lg p-2.5 text-sm text-slate-200 focus:ring-2 focus:ring-blue-500 outline-none"
                >
                  {ENVIRONMENTS.map(env => <option key={env} value={env}>{env}</option>)}
                </select>
              </div>

              <div className="flex-1">
                <label className="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1.5">Incident Prompt</label>
                <textarea 
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  placeholder="e.g., Why is payment-api failing right now?"
                  rows={6}
                  className="w-full bg-[#0F172A] border border-slate-700 rounded-lg p-3 text-sm text-slate-200 focus:ring-2 focus:ring-blue-500 outline-none resize-none placeholder-slate-600"
                />
              </div>

              <button 
                onClick={handleInvestigate}
                disabled={isInvestigating}
                className="w-full bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-bold py-3 rounded-lg shadow-[0_0_15px_rgba(79,70,229,0.3)] transition-all disabled:opacity-50 flex items-center justify-center gap-2"
              >
                {isInvestigating ? (
                  <>
                    <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                    Analyzing...
                  </>
                ) : 'Launch Investigation'}
              </button>
              
              {error && (
                <div className="p-3 bg-red-900/30 border border-red-800 rounded-lg text-red-300 text-xs mt-2">
                  {error}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* MIDDLE COL: Architecture Flowchart */}
        <div className="col-span-12 lg:col-span-5 bg-[#0F172A] relative flex items-center justify-center py-10 rounded-2xl border border-slate-800 shadow-inner overflow-hidden h-full">
          {/* Subtle grid background */}
          <div className="absolute inset-0 opacity-[0.03] pointer-events-none" style={{ backgroundImage: 'linear-gradient(#fff 1px, transparent 1px), linear-gradient(90deg, #fff 1px, transparent 1px)', backgroundSize: '20px 20px' }}></div>
          
          <div className="relative flex flex-col items-center w-full max-w-[500px] mx-auto pb-4 overflow-y-auto custom-scrollbar h-full px-2">
            
            {/* 1. User */}
            {renderNode("user", "User Prompt", "bg-slate-900/50 border-slate-600 text-slate-300", true)}

            <div className="h-6 border-l-2 border-dashed border-slate-600 z-0"></div>

            {/* 2. Orchestrator */}
            {renderNode("orchestrator", "Orchestrator Agent", "bg-emerald-600/20 border-emerald-500 text-emerald-400", !!plan || isInvestigating)}

            <div className="h-6 border-l-2 border-dashed border-slate-600 z-0"></div>

            {/* 3. Dispatcher */}
            <div className="relative w-full flex flex-col items-center z-10">
              {renderNode("dispatcher", "Dispatcher", "bg-slate-800 border-slate-500 text-slate-200", !!plan || isInvestigating)}
              
              {/* Branching Lines */}
              <div className="w-full flex justify-center mt-5 relative z-0">
                <div className="absolute top-0 w-[80%] h-px bg-slate-600 border-t-2 border-dashed border-slate-600"></div>
                <div className="absolute top-0 left-[10%] w-px h-5 bg-slate-600 border-l-2 border-dashed border-slate-600"></div>
                <div className="absolute top-0 left-1/2 w-px h-5 bg-slate-600 border-l-2 border-dashed border-slate-600 -translate-x-1/2"></div>
                <div className="absolute top-0 right-[10%] w-px h-5 bg-slate-600 border-l-2 border-dashed border-slate-600"></div>
              </div>

              {/* 4. Tools Row */}
              <div className="w-full flex justify-between px-[5%] mt-5 z-10">
                {renderNode("tool_log", "Log Search Tool", "bg-pink-600/20 border-pink-500 text-pink-400 !w-28 text-[11px] px-1", !!evidence || isInvestigating)}
                {renderNode("tool_event", "Event Search Tool", "bg-pink-600/20 border-pink-500 text-pink-400 !w-28 text-[11px] px-1", !!evidence || isInvestigating)}
                {renderNode("tool_metrics", "Metrics Tool", "bg-pink-600/20 border-pink-500 text-pink-400 !w-28 text-[11px] px-1", !!evidence || isInvestigating)}
              </div>

              {/* Merging Lines */}
              <div className="w-full flex justify-center mt-5 relative h-5 z-0">
                <div className="absolute bottom-0 w-[80%] h-px bg-slate-600 border-t-2 border-dashed border-slate-600"></div>
                <div className="absolute bottom-0 left-[10%] w-px h-5 bg-slate-600 border-l-2 border-dashed border-slate-600"></div>
                <div className="absolute bottom-0 left-1/2 w-px h-5 bg-slate-600 border-l-2 border-dashed border-slate-600 -translate-x-1/2"></div>
                <div className="absolute bottom-0 right-[10%] w-px h-5 bg-slate-600 border-l-2 border-dashed border-slate-600"></div>
              </div>
            </div>

            <div className="h-6 border-l-2 border-dashed border-slate-600 z-0"></div>

            {/* 5. Investigation Evidence Agent */}
            <div className="w-full flex flex-col items-center z-10">
              {renderNode("evidence_agent", "Investigation Evidence Agent", "bg-emerald-600/20 border-emerald-500 text-emerald-400", !!evidence || isInvestigating)}
            </div>

            <div className="h-6 border-l-2 border-dashed border-slate-600 z-0"></div>

            {/* 6. Correlation Engine */}
            {renderNode("correlation", "Correlation Engine", "bg-fuchsia-600/20 border-fuchsia-500 text-fuchsia-400", !!correlation || isInvestigating)}

            <div className="h-6 border-l-2 border-dashed border-slate-600 z-0"></div>

            {/* 7. Analysis Agent */}
            {renderNode("analysis", "Analysis Agent", "bg-cyan-600/20 border-cyan-500 text-cyan-400", !!analysis || isInvestigating)}

            {/* Branching Lines Out */}
            <div className="w-full flex justify-center mt-5 relative z-0">
              <div className="absolute top-0 w-[70%] h-px bg-slate-600 border-t-2 border-dashed border-slate-600"></div>
              <div className="absolute top-0 left-[15%] w-px h-5 bg-slate-600 border-l-2 border-dashed border-slate-600"></div>
              <div className="absolute top-0 left-1/2 w-px h-5 bg-slate-600 border-l-2 border-dashed border-slate-600 -translate-x-1/2"></div>
              <div className="absolute top-0 right-[15%] w-px h-5 bg-slate-600 border-l-2 border-dashed border-slate-600"></div>
            </div>

            {/* 7. Output Row */}
            <div className="w-full flex justify-between px-[10%] mt-5 z-10">
              {renderNode("timeline", "Timeline", "bg-slate-900/50 border-slate-400 text-slate-300 !w-24 text-[11px] px-1", !!correlation || isInvestigating)}
              {renderNode("relationships", "Relationships", "bg-slate-900/50 border-slate-400 text-slate-300 !w-28 text-[11px] px-1", !!correlation || isInvestigating)}
              {renderNode("signals", "Signals", "bg-slate-900/50 border-slate-400 text-slate-300 !w-24 text-[11px] px-1", !!correlation || isInvestigating)}
            </div>
            
          </div>
        </div>

        {/* RIGHT COL: Data Inspector */}
        <div className="col-span-12 lg:col-span-4 bg-slate-900/80 backdrop-blur p-6 rounded-2xl border border-slate-800 shadow-2xl overflow-hidden flex flex-col h-full">
          <div className="flex-1 overflow-y-auto custom-scrollbar pr-2">
            {renderDataInspector()}
          </div>
        </div>
        
      </main>
      
      {/* Custom Scrollbar Styles for this page */}
      <style dangerouslySetInnerHTML={{__html: `
        .custom-scrollbar::-webkit-scrollbar {
          width: 6px;
        }
        .custom-scrollbar::-webkit-scrollbar-track {
          background: rgba(15, 23, 42, 0.5); 
          border-radius: 4px;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb {
          background: rgba(71, 85, 105, 0.8); 
          border-radius: 4px;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover {
          background: rgba(100, 116, 139, 1); 
        }
      `}} />
      
      {/* Prompt Modal */}
      {showPromptModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/80 backdrop-blur-sm" onClick={() => setShowPromptModal(false)}>
          <div 
            className="bg-slate-800 border border-slate-700 rounded-xl shadow-2xl w-full max-w-4xl max-h-[80vh] flex flex-col overflow-hidden" 
            onClick={e => e.stopPropagation()}
          >
            <div className="p-4 border-b border-slate-700 flex justify-between items-center bg-slate-800/50">
              <h3 className="text-lg font-bold text-white flex items-center gap-2">
                <span className="text-cyan-400">🤖</span> Raw LLM Prompt Payload
              </h3>
              <button 
                onClick={() => setShowPromptModal(false)}
                className="text-slate-400 hover:text-white bg-slate-700/50 hover:bg-slate-700 rounded-full w-8 h-8 flex items-center justify-center transition-colors"
              >
                ✕
              </button>
            </div>
            <div className="p-4 overflow-y-auto custom-scrollbar flex-1">
              <pre className="text-xs text-slate-300 font-mono whitespace-pre-wrap break-words bg-slate-900/50 p-4 rounded-lg border border-slate-800">
                {rawPrompt}
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default App
