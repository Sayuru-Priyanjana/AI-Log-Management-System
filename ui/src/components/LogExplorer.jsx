import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import { getSystemLogs, getSystemLogsNLQ, getLogsContext, summarizeLogs, generateTimeline } from '../api';
import { useToast } from '../toast';

export default function LogExplorer({ systemId, services = [], timeframe, start: fallbackStart, end: fallbackEnd }) {
  const toast = useToast();
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [liveTail, setLiveTail] = useState(false);
  
  const [query, setQuery] = useState('');
  const [service, setService] = useState('');
  const [level, setLevel] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [cursorHistory, setCursorHistory] = useState([null]); // index 0 is null (page 1)
  const limit = 100;

  // New Features State
  const [contextMode, setContextMode] = useState(false);
  const [targetLog, setTargetLog] = useState(null);
  const [timeline, setTimeline] = useState(null);
  const [generatingTimeline, setGeneratingTimeline] = useState(false);
  const [aiSearchMode, setAiSearchMode] = useState(false);
  const [summary, setSummary] = useState(null);
  const [summarizing, setSummarizing] = useState(false);
  const [savedViews, setSavedViews] = useState([]);

  useEffect(() => {
    const loaded = localStorage.getItem('logintel_saved_views');
    if (loaded) {
      try { setSavedViews(JSON.parse(loaded)); } catch(e){}
    }
  }, []);

  const saveView = () => {
    const name = prompt('Enter a name for this saved view:');
    if (!name) return;
    const newViews = [...savedViews, { name, query, service, level }];
    setSavedViews(newViews);
    localStorage.setItem('logintel_saved_views', JSON.stringify(newViews));
    toast.success(`Saved view "${name}"`);
  };

  const applyView = (idx) => {
    if (idx === '') return;
    const view = savedViews[idx];
    if (view) {
      setQuery(view.query || '');
      setService(view.service || '');
      setLevel(view.level || '');
      setPage(1);
      setCursorHistory([null]);
      // fetchLogs will be triggered by useEffect dependencies
    }
  };

  const fetchLogs = async (showLoading = true, currentCursor = null, force = false) => {
    if (!systemId || (contextMode && !force)) return;
    if (showLoading) setLoading(true);
    setSummary(null);
    try {
      let activeStart = timeframe?.start || fallbackStart;
      let activeEnd = timeframe?.end || fallbackEnd;
      
      if (timeframe?.isRelative) {
        if (timeframe.relativeType?.type === 'seconds') {
          activeEnd = Math.floor(Date.now() / 1000);
          activeStart = activeEnd - timeframe.relativeType.value;
        } else if (timeframe.relativeType?.type === 'today') {
          const now = new Date();
          activeEnd = Math.floor(now.getTime() / 1000);
          activeStart = Math.floor(new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000);
        } else if (timeframe.relativeType?.type === 'thisWeek') {
          const now = new Date();
          activeEnd = Math.floor(now.getTime() / 1000);
          const day = now.getDay() || 7;
          if (day !== 1) now.setHours(-24 * (day - 1));
          activeStart = Math.floor(new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000);
        }
      }
      
      if (liveTail) {
        activeEnd = undefined; // Drop upper bound to never miss logs arriving exactly now
      }

      const cursorParam = currentCursor ? JSON.stringify(currentCursor) : null;
      let result;
      if (aiSearchMode && query.trim() !== '') {
        result = await getSystemLogsNLQ(systemId, { query, service, level, limit, start: activeStart, end: activeEnd });
      } else {
        result = await getSystemLogs(systemId, { query, service, level, limit, cursor: cursorParam, start: activeStart, end: activeEnd });
      }
      
      setLogs(result?.logs || []);
      setTotal(result?.total || 0);
    } catch (err) {
      toast.error('Failed to fetch logs', { detail: err.message });
      setLiveTail(false);
    } finally {
      if (showLoading) setLoading(false);
    }
  };

  const fetchContext = async (log) => {
    setLoading(true);
    setContextMode(true);
    setTargetLog(log);
    setTimeline(null);
    setSummary(null);
    setLiveTail(false);
    try {
      const timestampSeconds = Math.floor(new Date(log.timestamp).getTime() / 1000);
      const result = await getLogsContext(systemId, timestampSeconds, log.service || '');
      const contextLogs = Array.isArray(result) ? result : (result?.logs || []);
      setLogs(contextLogs);
      setTotal(contextLogs.length);
    } catch (err) {
      toast.error('Failed to fetch context', { detail: err.message });
      setContextMode(false);
    } finally {
      setLoading(false);
    }
  };

  const handleSummarize = async () => {
    if (logs.length === 0) return;
    setSummarizing(true);
    try {
      const result = await summarizeLogs(systemId, logs);
      setSummary(result.summary);
    } catch (err) {
      toast.error('Summarization failed', { detail: err.message });
    } finally {
      setSummarizing(false);
    }
  };

  const exitContextMode = () => {
    setContextMode(false);
    setTargetLog(null);
    setTimeline(null);
    setPage(1);
    setCursorHistory([null]);
    fetchLogs(true, null, true);
  };

  useEffect(() => {
    if (!contextMode) {
      setTargetLog(null);
      setTimeline(null);
      setPage(1);
      setCursorHistory([null]);
      fetchLogs(true, null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [systemId, timeframe?.start, timeframe?.end, fallbackStart, fallbackEnd, query, service, level]);

  useEffect(() => {
    let interval;
    if (liveTail && !contextMode) {
      interval = setInterval(() => {
        fetchLogs(false, null);
      }, 5000);
    }
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveTail, contextMode, systemId, query, service, level, page]);

  const handleSearch = (e) => {
    e.preventDefault();
    if (contextMode) {
      setContextMode(false);
      setTargetLog(null);
      setTimeline(null);
    }
    setPage(1);
    setCursorHistory([null]);
    fetchLogs(true, null);
  };

  const getLevelColor = (lvl) => {
    const upper = String(lvl).toUpperCase();
    if (upper === 'ERROR' || upper === 'FATAL') return 'var(--err)';
    if (upper === 'WARN' || upper === 'WARNING') return 'var(--warn)';
    if (upper === 'INFO') return 'var(--ok)';
    return 'var(--text-3)';
  };

  const handlePrevPage = () => {
    if (page === 1 || contextMode) return;
    const newPage = page - 1;
    setPage(newPage);
    fetchLogs(true, cursorHistory[newPage - 1]);
  };

  const handleNextPage = () => {
    if (logs.length === 0 || page >= Math.ceil(total / limit) || contextMode) return;
    const lastLog = logs[logs.length - 1];
    const nextCursor = lastLog.sort || null;
    
    // Ensure we have room in the history array
    const newHistory = [...cursorHistory];
    newHistory[page] = nextCursor;
    setCursorHistory(newHistory);
    
    const newPage = page + 1;
    setPage(newPage);
    fetchLogs(true, nextCursor);
  };

  return (
    <div className="card card--fill" style={{ display: 'flex', flexDirection: 'column', height: '100%', flexShrink: 0 }}>
      <header style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: '16px', backgroundColor: 'var(--surface-2)', overflowX: 'auto' }}>
        
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexShrink: 0 }}>
          <h3 style={{ margin: 0, color: 'var(--text)', whiteSpace: 'nowrap' }}>Log Explorer</h3>
          {contextMode && (
            <span style={{ fontSize: '13px', color: 'var(--warn)', fontWeight: 'bold', display: 'flex', alignItems: 'center', gap: '6px', whiteSpace: 'nowrap' }}>
              <span className="dot dot--warn"></span> Context View
            </span>
          )}
        </div>

        <div style={{ width: '1px', height: '24px', backgroundColor: 'var(--border)', flexShrink: 0 }} />

        <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexShrink: 0, backgroundColor: 'var(--surface-3)', padding: '4px', borderRadius: '4px' }}>
          <button 
            type="button" 
            onClick={() => setAiSearchMode(false)}
            className={`btn btn--sm ${!aiSearchMode ? 'btn--primary' : ''}`}
            style={{ padding: '2px 8px', fontSize: '12px', minWidth: 'auto', opacity: !aiSearchMode ? 1 : 0.6 }}
          >
            Raw Query
          </button>
          <button 
            type="button" 
            onClick={() => setAiSearchMode(true)}
            className={`btn btn--sm ${aiSearchMode ? 'btn--primary' : ''}`}
            style={{ padding: '2px 8px', fontSize: '12px', minWidth: 'auto', opacity: aiSearchMode ? 1 : 0.6 }}
            title="Use Natural Language Search"
          >
            AI Search ✨
          </button>
        </div>

        <form onSubmit={handleSearch} style={{ display: 'flex', gap: '8px', flex: 1, alignItems: 'center', minWidth: '400px' }}>
          <div style={{ position: 'relative', flex: 1, minWidth: '200px' }}>
            <span style={{ position: 'absolute', left: '10px', top: '50%', transform: 'translateY(-50%)', opacity: 0.5, fontSize: '14px' }}>🔍</span>
            <input 
              type="text" 
              className="input input--sm" 
              placeholder="Search keywords..." 
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{ width: '100%', paddingLeft: '28px', backgroundColor: 'var(--surface)' }}
            />
          </div>
          
          <select className="input input--sm" value={service} onChange={(e) => setService(e.target.value)} style={{ width: '140px', backgroundColor: 'var(--surface)', flexShrink: 0 }}>
            <option value="">All Services</option>
            {services.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          
          <select className="input input--sm" value={level} onChange={(e) => { setLevel(e.target.value); setPage(1); setCursorHistory([null]); }} style={{ width: '110px', backgroundColor: 'var(--surface)', flexShrink: 0 }}>
            <option value="">All Levels</option>
            <option value="error">ERROR</option>
            <option value="warn">WARN</option>
            <option value="info">INFO</option>
            <option value="debug">DEBUG</option>
            <option value="trace">TRACE</option>
          </select>
          
          <button type="submit" className="btn btn--sm btn--primary" style={{ flexShrink: 0 }}>Search</button>
        </form>

        <div style={{ width: '1px', height: '24px', backgroundColor: 'var(--border)', flexShrink: 0 }} />

        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexShrink: 0 }}>
          <select className="input input--sm" onChange={(e) => applyView(e.target.value)} value="" style={{ width: '130px', backgroundColor: 'var(--surface)' }}>
            <option value="">Bookmarks...</option>
            {savedViews.map((v, i) => <option key={i} value={i}>{v.name}</option>)}
          </select>
          <button type="button" className="btn btn--sm" onClick={saveView} title="Save current filters as bookmark">💾 Save</button>
        </div>

        <div style={{ width: '1px', height: '24px', backgroundColor: 'var(--border)', flexShrink: 0 }} />

        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexShrink: 0 }}>
          {contextMode && (
            <>
              <button 
                type="button" 
                className="btn btn--sm" 
                onClick={async () => {
                  if (!targetLog) return;
                  setGeneratingTimeline(true);
                  try {
                    const ts = Math.floor(new Date(targetLog.timestamp).getTime() / 1000);
                    const res = await generateTimeline(systemId, ts, targetLog.service || '');
                    setTimeline(res.timeline);
                  } catch (err) {
                    toast.error('Failed to generate timeline', { detail: err.message });
                  } finally {
                    setGeneratingTimeline(false);
                  }
                }} 
                disabled={generatingTimeline}
                style={{ borderColor: 'var(--brand)', color: 'var(--brand)' }}
              >
                {generatingTimeline ? '⏳ Analyzing...' : '🔍 Analyze Root Cause'}
              </button>
              <button type="button" className="btn btn--sm" style={{ borderColor: 'var(--warn)', color: 'var(--warn)' }} onClick={exitContextMode}>✕ Exit Context</button>
            </>
          )}
          
          <button 
            type="button" 
            className="btn btn--sm" 
            onClick={handleSummarize} 
            disabled={summarizing || logs.length === 0}
            style={{ display: 'flex', alignItems: 'center', gap: '6px', backgroundColor: 'rgba(59, 130, 246, 0.1)', color: 'var(--brand)', borderColor: 'var(--brand)' }}
          >
            {summarizing ? '⏳ Summarizing...' : '✨ AI Summarize'}
          </button>
          
          <button 
            type="button" 
            className={`btn btn--sm ${liveTail ? 'btn--primary' : ''}`}
            onClick={() => setLiveTail(!liveTail)}
            style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
          >
            {liveTail && <span className="dot dot--ok" style={{ animation: 'toast-in 1s infinite alternate' }} />}
            Live Tail {liveTail ? 'ON' : 'OFF'}
          </button>
        </div>
      </header>
      
      {summary && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 9999, backgroundColor: 'rgba(0,0,0,0.85)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '40px' }}>
          <div style={{ background: 'var(--bg)', width: '100%', maxWidth: '800px', maxHeight: '80vh', borderRadius: '8px', display: 'flex', flexDirection: 'column', boxShadow: '0 10px 40px rgba(0,0,0,0.8)', border: '1px solid var(--border)' }}>
            <header style={{ padding: '16px 24px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <h3 style={{ margin: 0, color: 'var(--brand)', display: 'flex', alignItems: 'center', gap: '8px' }}>✨ AI Log Summary</h3>
              <button className="btn btn--sm" onClick={() => setSummary(null)}>✕ Close</button>
            </header>
            <div className="markdown-body" style={{ padding: '24px', overflowY: 'auto', color: 'var(--text)', lineHeight: '1.6', fontSize: '14px' }}>
              <ReactMarkdown>{summary}</ReactMarkdown>
            </div>
          </div>
        </div>
      )}

      {timeline && (
        <div style={{ padding: '24px', backgroundColor: 'var(--surface-3)', borderBottom: '1px solid var(--border)' }}>
          <h4 style={{ margin: '0 0 12px 0', color: 'var(--brand)', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span>🔍</span> Root Cause Timeline
          </h4>
          <div className="markdown-body" style={{ color: 'var(--text)', fontSize: '14px', lineHeight: '1.6' }}>
            <ReactMarkdown>{timeline}</ReactMarkdown>
          </div>
        </div>
      )}

      <div className="card-body" style={{ flex: 1, padding: 0, overflow: 'auto', backgroundColor: 'var(--bg)' }}>
        {loading ? (
          <div className="empty" style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>Loading logs...</div>
        ) : logs.length === 0 ? (
          <div className="empty" style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>No logs found matching your criteria.</div>
        ) : (
          <table className="table" style={{ width: '100%', textAlign: 'left', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
            <thead style={{ position: 'sticky', top: 0, backgroundColor: 'var(--surface)', zIndex: 1, borderBottom: '1px solid var(--border)' }}>
              <tr>
                <th style={{ width: '180px', padding: '10px 16px', color: 'var(--text-2)', fontWeight: 600, borderRight: '1px solid var(--border)' }}>Timestamp</th>
                <th style={{ width: '90px', padding: '10px 16px', color: 'var(--text-2)', fontWeight: 600, borderRight: '1px solid var(--border)' }}>Level</th>
                <th style={{ width: '160px', padding: '10px 16px', color: 'var(--text-2)', fontWeight: 600, borderRight: '1px solid var(--border)' }}>Service</th>
                <th style={{ padding: '10px 16px', color: 'var(--text-2)', fontWeight: 600 }}>Message</th>
                <th style={{ width: '40px', padding: '10px 8px', textAlign: 'center' }}></th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log) => (
                <tr key={log.id} style={{ borderBottom: '1px solid var(--border)' }} className="table-row-hover">
                  <td style={{ padding: '8px 16px', color: 'var(--text-2)', fontSize: '12px', whiteSpace: 'nowrap' }}>
                    {new Date(log.timestamp).toLocaleString()}
                  </td>
                  <td style={{ padding: '8px 16px', fontWeight: 600, fontSize: '12px', whiteSpace: 'nowrap', color: getLevelColor(log.level) }}>
                    {log.level || <span style={{ color: 'var(--text-3)' }}>-</span>}
                  </td>
                  <td style={{ padding: '8px 16px', color: 'var(--text)', fontSize: '13px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {log.service}
                  </td>
                  <td style={{ padding: '8px 16px', color: 'var(--text-2)', fontFamily: 'var(--mono)', fontSize: '13px', wordBreak: 'break-word' }}>
                    {typeof log.message === 'object' ? JSON.stringify(log.message) : String(log.message || '')}
                  </td>
                  <td style={{ padding: '8px', textAlign: 'center' }}>
                    {!contextMode && (
                      <button 
                        className="btn btn--sm" 
                        style={{ padding: '4px', background: 'transparent', border: 'none', cursor: 'pointer' }} 
                        onClick={() => fetchContext(log)}
                        title="View Surrounding Logs"
                      >
                        👁️
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {!contextMode && total > limit && (
        <footer style={{ padding: '12px 16px', borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', backgroundColor: 'var(--surface)' }}>
          <div style={{ color: 'var(--text-2)', fontSize: '13px' }}>
            Showing {((page - 1) * limit) + 1} to {Math.min(page * limit, total)} of {total} logs
          </div>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button 
              className="btn btn--sm" 
              disabled={page === 1}
              onClick={handlePrevPage}
            >
              Previous
            </button>
            <span style={{ display: 'flex', alignItems: 'center', fontSize: '13px', color: 'var(--text)' }}>
              Page {page} of {Math.ceil(total / limit)}
            </span>
            <button 
              className="btn btn--sm" 
              disabled={page >= Math.ceil(total / limit)}
              onClick={handleNextPage}
            >
              Next
            </button>
          </div>
        </footer>
      )}
    </div>
  );
}
