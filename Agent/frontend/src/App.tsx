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
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleInvestigate = async () => {
    if (!question.trim()) {
      setError("Please enter a question.")
      return
    }

    setLoading(true)
    setError(null)
    setPlan(null)

    const systemName = SYSTEMS.find(s => s.id === systemId)?.name || systemId

    try {
      const response = await fetch("http://localhost:8000/api/investigations", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
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

      const data = await response.json()
      setPlan(data.plan)
    } catch (err: any) {
      if (err.message === "Failed to fetch") {
        setError("Unable to contact the local AI model or API backend. Make sure the backend and Ollama are running.")
      } else {
        setError(err.message || "An unexpected error occurred.")
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 p-8 font-sans">
      <div className="max-w-4xl mx-auto space-y-8">
        <header className="border-b border-slate-700 pb-4">
          <h1 className="text-3xl font-bold bg-gradient-to-r from-blue-400 to-indigo-400 bg-clip-text text-transparent">
            AI Log Analysis System
          </h1>
          <p className="text-slate-400 mt-2">Phase 1: Orchestrator Agent</p>
        </header>

        <main className="grid grid-cols-1 md:grid-cols-2 gap-8">
          <div className="space-y-6 bg-slate-800 p-6 rounded-xl border border-slate-700 shadow-xl">
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
                disabled={loading}
                className="w-full bg-blue-600 hover:bg-blue-500 text-white font-medium py-3 px-4 rounded-lg transition-colors disabled:opacity-50 flex justify-center items-center gap-2"
              >
                {loading ? (
                  <>
                    <svg className="animate-spin h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    Thinking...
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
            <h2 className="text-xl font-semibold text-white">Investigation Plan</h2>
            
            {plan ? (
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

                  <div className="p-4">
                    <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Required Data</h3>
                    <ul className="space-y-2">
                      {plan.required_data.map(data => (
                        <li key={data} className="flex items-center text-slate-300 text-sm">
                          <svg className="h-5 w-5 text-emerald-400 mr-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                          </svg>
                          {data.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase())}
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="p-4 bg-slate-750">
                    <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Investigation Goal</h3>
                    <p className="text-white font-medium">{plan.investigation_goal}</p>
                  </div>
                </div>
              </div>
            ) : (
              <div className="h-64 border-2 border-dashed border-slate-700 rounded-xl flex items-center justify-center text-slate-500">
                <p>No plan generated yet.</p>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  )
}

export default App
