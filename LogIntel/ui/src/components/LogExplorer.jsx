import React, { useState, useEffect, useRef } from 'react';
import { getSystemLogs } from '../api';
import { useToast } from '../toast';

export default function LogExplorer({ systemId, services = [] }) {
  const toast = useToast();
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [liveTail, setLiveTail] = useState(false);
  
  const [query, setQuery] = useState('');
  const [service, setService] = useState('');
  const [level, setLevel] = useState('');
  
  const fetchLogs = async (showLoading = true) => {
    if (!systemId) return;
    if (showLoading) setLoading(true);
    try {
      const result = await getSystemLogs(systemId, { query, service, level, limit: 100 });
      setLogs(result || []);
    } catch (err) {
      toast.error('Failed to fetch logs', { detail: err.message });
      setLiveTail(false);
    } finally {
      if (showLoading) setLoading(false);
    }
  };

  useEffect(() => {
    fetchLogs(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [systemId]);

  useEffect(() => {
    let interval;
    if (liveTail) {
      interval = setInterval(() => {
        fetchLogs(false);
      }, 5000);
    }
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveTail, systemId, query, service, level]);

  const handleSearch = (e) => {
    e.preventDefault();
    fetchLogs(true);
  };

  const getLevelColor = (lvl) => {
    const upper = String(lvl).toUpperCase();
    if (upper === 'ERROR' || upper === 'FATAL') return 'var(--err)';
    if (upper === 'WARN' || upper === 'WARNING') return 'var(--warn)';
    if (upper === 'INFO') return 'var(--ok)';
    return 'var(--text-3)';
  };

  return (
    <div className="card card--fill" style={{ display: 'flex', flexDirection: 'column', height: '500px', flexShrink: 0 }}>
      <header style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', flexWrap: 'nowrap', gap: '16px', backgroundColor: 'var(--surface-2)' }}>
        <h3 style={{ margin: 0, color: 'var(--text)' }}>Log Explorer</h3>
        <span className="spacer" />
        
        <form onSubmit={handleSearch} style={{ display: 'flex', gap: '8px', flexWrap: 'nowrap', alignItems: 'center' }}>
          <input 
            type="text" 
            className="input input--sm" 
            placeholder="Search keywords..." 
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ width: '240px', backgroundColor: 'var(--surface)' }}
          />
          <select className="input input--sm" value={service} onChange={(e) => setService(e.target.value)} style={{ width: 'auto', backgroundColor: 'var(--surface)' }}>
            <option value="">All Services</option>
            {services.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="input input--sm" value={level} onChange={(e) => setLevel(e.target.value)} style={{ width: 'auto', backgroundColor: 'var(--surface)' }}>
            <option value="">All Levels</option>
            <option value="error">ERROR</option>
            <option value="warn">WARN</option>
            <option value="info">INFO</option>
            <option value="debug">DEBUG</option>
          </select>
          <button type="submit" className="btn btn--sm btn--primary">Search</button>
          
          <button 
            type="button" 
            className={`btn btn--sm ${liveTail ? 'btn--primary' : ''}`}
            onClick={() => setLiveTail(!liveTail)}
            style={{ display: 'flex', alignItems: 'center', gap: '5px' }}
          >
            {liveTail && <span className="dot dot--ok" style={{ animation: 'toast-in 1s infinite alternate' }} />}
            Live Tail {liveTail ? 'ON' : 'OFF'}
          </button>
        </form>
      </header>
      
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
              </tr>
            </thead>
            <tbody>
              {logs.map((log) => (
                <tr key={log.id} style={{ borderBottom: '1px solid var(--border)' }} className="table-row-hover">
                  <td style={{ padding: '8px 16px', color: 'var(--text-2)', fontSize: '12px', whiteSpace: 'nowrap' }}>
                    {new Date(log.timestamp).toLocaleString()}
                  </td>
                  <td style={{ padding: '8px 16px', fontWeight: 600, fontSize: '12px', color: getLevelColor(log.level) }}>
                    {(log.level || 'INFO').toUpperCase()}
                  </td>
                  <td style={{ padding: '8px 16px', color: 'var(--text)', fontSize: '13px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {log.service}
                  </td>
                  <td style={{ padding: '8px 16px', color: 'var(--text-2)', fontFamily: 'var(--mono)', fontSize: '13px', wordBreak: 'break-word' }}>
                    {typeof log.message === 'object' ? JSON.stringify(log.message) : String(log.message || '')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
