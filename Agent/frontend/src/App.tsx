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

interface InvestigationEvidence {
  application_logs: ApplicationLogEvidence[];
  kubernetes_events: KubernetesEventEvidence[];
  status: Record<string, string>;
  queries: Record<string, any>;
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
  
  const [loadingPlan, setLoadingPlan] = useState(false)
  const [loadingEvidence, setLoadingEvidence] = useState(false)
  const [error, setError] = useState<string | null>(null)
  
  // Collapse state for evidence
  const [showLogs, setShowLogs] = useState(false)
  const [showEvents, setShowEvents] = useState(false)
  const [showQueries, setShowQueries] = useState(false)

  const handleInvestigate = async () => {
    if (!question.trim()) {
      setError("Please enter a question.")
      return
    }

    setLoadingPlan(true)
    setError(null)
    setPlan(null)
    setEvidence(null)

    const systemName = SYSTEMS.find(s => s.id === systemId)?.name || systemId

    try {
      // 1. Get the Investigation Plan from Orchestrator
      const planResponse = await fetch("http://localhost:8000/api/investigations/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          system_id: systemId,
          system_name: systemName,
          environment,
          question
        })
      })

      if (!planResponse.ok) {
        const data = await planResponse.json().catch(() => null)
        throw new Error(data?.detail || `HTTP error! status: ${planResponse.status}`)
      }

      const planData = await planResponse.json()
      setPlan(planData.plan)
      setLoadingPlan(false)

      // 2. Fetch Evidence using the tools
      setLoadingEvidence(true)
      const evidenceResponse = await fetch("http://localhost:8000/api/investigations/evidence", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(planData.plan)
      })

      if (!evidenceResponse.ok) {
        const data = await evidenceResponse.json().catch(() => null)
        throw new Error(data?.detail || `HTTP error! status: ${evidenceResponse.status}`)
      }

      const evidenceData = await evidenceResponse.json()
      setEvidence(evidenceData.evidence)

    } catch (err: any) {
      if (err.message === "Failed to fetch") {
        setError("Unable to contact the local AI model or API backend. Make sure the backend and Ollama are running.")
      } else {
        setError(err.message || "An unexpected error occurred.")
      }
    } finally {
      setLoadingPlan(false)
      setLoadingEvidence(false)
    }
  }

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 p-8 font-sans pb-24">
      <div className="max-w-5xl mx-auto space-y-8">
        <header className="border-b border-slate-700 pb-4">
          <h1 className="text-3xl font-bold bg-gradient-to-r from-blue-400 to-indigo-400 bg-clip-text text-transparent">
            AI Log Analysis System
          </h1>
          <p className="text-slate-400 mt-2">Phase 2: Evidence Gathering</p>
        </header>

        <main className="grid grid-cols-1 md:grid-cols-2 gap-8">
          <div className="space-y-6 bg-slate-800 p-6 rounded-xl border border-slate-700 shadow-xl self-start">
            <h2 className="text-xl font-semibold text-white">Investigation Request</h2>
            
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">System</label>
                <select 
                  value={systemId} 
                  onChange={(e) => setSystemId(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-slate-200 focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all"
                >
                  {SYSTEMS.map(sys => (
                    <option key={sys.id} value={sys.id}>{sys.name}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">Environment</label>
                <select 
                  value={environment} 
                  onChange={(e) => setEnvironment(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg p-2.5 text-slate-200 focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all"
                >
                  {ENVIRONMENTS.map(env => (
                    <option key={env} value={env}>{env}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">Question</label>
                <textarea 
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  placeholder="Why is payment-api failing?"
                  rows={4}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg p-3 text-slate-200 focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all resize-none placeholder-slate-500"
                />
              </div>

              <button 
                onClick={handleInvestigate}
                disabled={loadingPlan || loadingEvidence}
                className="w-full bg-blue-600 hover:bg-blue-500 text-white font-medium py-3 px-4 rounded-lg transition-colors disabled:opacity-50 flex justify-center items-center gap-2"
              >
                {loadingPlan ? (
                  <>
                    <svg className="animate-spin h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    Thinking (Orchestrator)...
                  </>
                ) : loadingEvidence ? (
                  <>
                    <svg className="animate-spin h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    Gathering Evidence (Tools)...
                  </>
                ) : (
                  'Investigate'
                )}
              </button>
            </div>
            
            {error && (
              <div className="p-4 bg-red-900/30 border border-red-800 rounded-lg text-red-300 text-sm">
                {error}
              </div>
            )}
          </div>

          <div className="space-y-6">
            <h2 className="text-xl font-semibold text-white">Results</h2>
            
            {plan ? (
              <div className="space-y-6">
                <div className="bg-slate-800 rounded-xl border border-slate-700 shadow-xl overflow-hidden">
                  <div className="divide-y divide-slate-700/50">
                    <div className="p-4">
                      <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Intent</h3>
                      <p className="text-indigo-300 font-medium">{plan.intent}</p>
                    </div>
                    
                    <div className="p-4 grid grid-cols-2 gap-4">
                      <div>
                        <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">System</h3>
                        <p className="text-slate-200">{plan.system_id}</p>
                      </div>
                      <div>
                        <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Environment</h3>
                        <p className="text-slate-200">{plan.environment}</p>
                      </div>
                    </div>

                    <div className="p-4 grid grid-cols-2 gap-4">
                      <div>
                        <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Service</h3>
                        <p className="text-slate-200">{plan.service || 'N/A'}</p>
                      </div>
                      <div>
                        <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Time Range</h3>
                        <p className="text-slate-200">
                          {plan.time_range.duration ? plan.time_range.duration : 
                           plan.time_range.start ? `${plan.time_range.start} to ${plan.time_range.end}` : 
                           plan.time_range.type}
                        </p>
                      </div>
                    </div>

                    <div className="p-4 bg-slate-750">
                      <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Investigation Goal</h3>
                      <p className="text-white font-medium">{plan.investigation_goal}</p>
                    </div>
                  </div>
                </div>
                
                {loadingEvidence ? (
                  <div className="bg-slate-800 rounded-xl border border-slate-700 shadow-xl p-8 flex flex-col items-center justify-center gap-4 animate-pulse">
                    <svg className="animate-spin h-8 w-8 text-blue-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    <p className="text-slate-400 font-medium">Tools are executing queries...</p>
                  </div>
                ) : evidence && (
                  <div className="bg-slate-800 rounded-xl border border-slate-700 shadow-xl overflow-hidden p-6 space-y-6">
                    <h3 className="text-lg font-semibold text-white border-b border-slate-700 pb-2">Evidence Retrieved</h3>
                    
                    {/* Queries Executed View */}
                    {evidence.queries && Object.keys(evidence.queries).length > 0 && (
                      <div>
                        <div 
                          className="flex justify-between items-center bg-slate-900 p-3 rounded-lg cursor-pointer hover:bg-slate-700 transition"
                          onClick={() => setShowQueries(!showQueries)}
                        >
                          <span className="font-medium text-amber-400 flex items-center gap-2">
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" /></svg>
                            Raw OpenSearch Queries
                          </span>
                        </div>
                        {showQueries && (
                          <div className="mt-3 max-h-60 overflow-y-auto space-y-3 pr-2 custom-scrollbar">
                            {Object.entries(evidence.queries).map(([tool, query], i) => (
                              <div key={tool} className="space-y-1">
                                <span className="text-xs text-slate-400 uppercase tracking-wider font-bold ml-1">{tool}</span>
                                <pre className="text-[10px] font-mono bg-slate-950 p-3 rounded text-slate-300 overflow-x-auto border border-slate-800 whitespace-pre-wrap">
                                  {JSON.stringify(query, null, 2)}
                                </pre>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Logs View */}
                    {plan.required_data.includes('application_logs') && (
                      <div>
                        <div 
                          className="flex justify-between items-center bg-slate-900 p-3 rounded-lg cursor-pointer hover:bg-slate-700 transition"
                          onClick={() => setShowLogs(!showLogs)}
                        >
                          <span className="font-medium text-slate-300">Application Logs</span>
                          <span className="bg-blue-600 text-white text-xs px-2 py-1 rounded-full">
                            {evidence.application_logs.length} records
                          </span>
                        </div>
                        {showLogs && (
                          <div className="mt-3 max-h-60 overflow-y-auto space-y-2 pr-2 custom-scrollbar">
                            {evidence.application_logs.length === 0 ? (
                              <p className="text-sm text-slate-500 italic">No logs found or OpenSearch error.</p>
                            ) : (
                              evidence.application_logs.map((log, i) => (
                                <div key={i} className="text-xs font-mono bg-slate-900 p-2 rounded text-slate-300 flex flex-col gap-1 border border-slate-800">
                                  <div className="flex gap-2 items-center text-slate-500">
                                    <span>{new Date(log.timestamp).toLocaleTimeString()}</span>
                                    {log.level && (
                                      <span className={`px-1.5 py-0.5 rounded text-[10px] uppercase ${log.level.toLowerCase() === 'error' ? 'bg-red-900/50 text-red-300' : 'bg-slate-700'}`}>
                                        {log.level}
                                      </span>
                                    )}
                                    <span className="text-indigo-400">{log.service_name || 'unknown'}</span>
                                  </div>
                                  <p className="text-slate-200">{log.message}</p>
                                </div>
                              ))
                            )}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Events View */}
                    {plan.required_data.includes('kubernetes_events') && (
                      <div>
                        <div 
                          className="flex justify-between items-center bg-slate-900 p-3 rounded-lg cursor-pointer hover:bg-slate-700 transition"
                          onClick={() => setShowEvents(!showEvents)}
                        >
                          <span className="font-medium text-slate-300">Kubernetes Events</span>
                          <span className="bg-blue-600 text-white text-xs px-2 py-1 rounded-full">
                            {evidence.kubernetes_events.length} records
                          </span>
                        </div>
                        {showEvents && (
                          <div className="mt-3 max-h-60 overflow-y-auto space-y-2 pr-2 custom-scrollbar">
                            {evidence.kubernetes_events.length === 0 ? (
                              <p className="text-sm text-slate-500 italic">No events found or OpenSearch error.</p>
                            ) : (
                              evidence.kubernetes_events.map((evt, i) => (
                                <div key={i} className="text-xs font-mono bg-slate-900 p-2 rounded text-slate-300 flex flex-col gap-1 border border-slate-800">
                                  <div className="flex gap-2 items-center text-slate-500">
                                    <span>{new Date(evt.timestamp).toLocaleTimeString()}</span>
                                    {evt.reason && (
                                      <span className={`px-1.5 py-0.5 rounded text-[10px] ${evt.reason.includes('Failed') || evt.reason.includes('BackOff') ? 'bg-red-900/50 text-red-300' : 'bg-slate-700'}`}>
                                        {evt.reason}
                                      </span>
                                    )}
                                    {evt.action && <span className="text-emerald-400">{evt.action}</span>}
                                  </div>
                                  <p className="text-slate-200">{evt.message}</p>
                                </div>
                              ))
                            )}
                          </div>
                        )}
                      </div>
                    )}
                    
                  </div>
                )}
              </div>
            ) : (
              <div className="h-64 border-2 border-dashed border-slate-700 rounded-xl flex items-center justify-center text-slate-500">
                <p>No results yet.</p>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  )
}

export default App
