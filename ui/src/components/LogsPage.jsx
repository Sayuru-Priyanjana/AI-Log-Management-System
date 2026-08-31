import React, { useState, useEffect } from 'react';
import { getSystems, getSystemMetricsLogs } from '../api';
import { useToast } from '../toast';
import { useNavigate } from 'react-router-dom';
import LogExplorer from './LogExplorer';
import TimeframePicker from './TimeframePicker';

export default function LogsPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [systems, setSystems] = useState([]);
  const [selectedId, setSelectedId] = useState(() => localStorage.getItem('lastSystemId') || '');
  const [services, setServices] = useState([]);
  
  // Default to last 24 hours
  const defaultEnd = Math.floor(Date.now() / 1000);
  const defaultStart = defaultEnd - (24 * 3600);
  
  const [timeframe, setTimeframe] = useState({
    start: defaultStart,
    end: defaultEnd,
    label: 'Last 24 hours',
    isRelative: true,
    relativeType: { type: 'seconds', value: 24 * 3600 }
  });

  // Fetch systems on mount
  useEffect(() => {
    let mounted = true;
    getSystems()
      .then((systemData) => {
        if (!mounted) return;
        const list = systemData.systems || [];
        setSystems(list);
        setSelectedId((current) => {
          const next = list.some((s) => s.id === current) ? current : (list[0]?.id || null);
          if (next) localStorage.setItem('lastSystemId', next);
          return next;
        });
      })
      .catch((err) => mounted && toast.error('Could not load systems', { detail: err.message }));
    return () => { mounted = false; };
  }, [toast]);

  // Fetch services when system or timeframe changes
  useEffect(() => {
    let canceled = false;
    
    async function loadServices() {
      if (!selectedId) return;
      
      try {
        const logsRes = await getSystemMetricsLogs(selectedId, timeframe.start, timeframe.end).catch(() => []);
        if (!canceled) {
          const svcSet = new Set();
          logsRes.forEach(point => {
            Object.keys(point).forEach(k => {
              if (k !== 'time') svcSet.add(k);
            });
          });
          setServices(Array.from(svcSet));
        }
      } catch (err) {
        console.error('Failed to load services for logs page', err);
      }
    }
    
    loadServices();
    return () => { canceled = true; };
  }, [selectedId, timeframe.start, timeframe.end]);

  const selectSystem = (id) => {
    setSelectedId(id);
    if (id) localStorage.setItem('lastSystemId', id);
  };

  const handleTimeframeChange = (tf) => {
    setTimeframe(tf);
  };

  return (
    <div className="wsx" style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 46px)' }}>
      <div className="wsx-top" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%', gap: '16px' }}>
        <select className="input" value={selectedId || ''} disabled={systems.length === 0}
          onChange={(e) => selectSystem(e.target.value)}>
          {systems.length === 0 && <option value="">No systems discovered yet</option>}
          {systems.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        
        <div style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
          <TimeframePicker onChange={handleTimeframeChange} defaultLabel="Last 24 hours" />
          <button onClick={() => navigate('/dashboard')} className="btn btn--primary btn--sm" style={{ padding: '2px 12px', fontWeight: 'bold', height: '32px' }}>
            Go to Dashboard →
          </button>
        </div>
      </div>

      <div className="wsx-body" style={{ flex: 1, padding: '16px', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        {selectedId ? (
          <LogExplorer systemId={selectedId} services={services} timeframe={timeframe} />
        ) : (
          <div className="empty-state" style={{ height: '100%' }}>
            <div className="empty-state-desc">No systems have shipped logs yet. Once one does, it appears here automatically.</div>
          </div>
        )}
      </div>
    </div>
  );
}
