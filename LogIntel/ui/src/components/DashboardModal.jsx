import React, { useState, useEffect } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer
} from 'recharts';
import { getSystemMetricsRequests, getSystemMetricsRam, getSystemMetricsLogs } from '../api';

const COLORS = [
  '#0ea5e9', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
  '#ec4899', '#f97316', '#14b8a6', '#6366f1', '#84cc16'
];

export default function DashboardModal({ systems, initialSystemId, onClose }) {
  const [selectedId, setSelectedId] = useState(initialSystemId || (systems[0]?.id || ''));
  const [hours, setHours] = useState(24);
  
  const [cpuData, setCpuData] = useState([]);
  const [ramData, setRamData] = useState([]);
  const [logsData, setLogsData] = useState([]);
  const [loading, setLoading] = useState(false);

  const [cpuServices, setCpuServices] = useState([]);
  const [ramServices, setRamServices] = useState([]);
  const [logsServices, setLogsServices] = useState([]);

  useEffect(() => {
    let canceled = false;
    
    async function loadAllMetrics() {
      if (!selectedId) return;
      setLoading(true);
      
      const end = Math.floor(Date.now() / 1000);
      const start = end - (hours * 3600);
      
      try {
        const [cpuRes, ramRes, logsRes] = await Promise.all([
          getSystemMetricsRequests(selectedId, start, end).catch(() => []),
          getSystemMetricsRam(selectedId, start, end).catch(() => []),
          getSystemMetricsLogs(selectedId, start, end).catch(() => [])
        ]);
        
        if (!canceled) {
          setCpuData(cpuRes);
          setRamData(ramRes);
          setLogsData(logsRes);
          
          const extractServices = (data) => {
            const svcSet = new Set();
            data.forEach(point => {
              Object.keys(point).forEach(k => {
                if (k !== 'time') svcSet.add(k);
              });
            });
            return Array.from(svcSet);
          };
          
          setCpuServices(extractServices(cpuRes));
          setRamServices(extractServices(ramRes));
          setLogsServices(extractServices(logsRes));
        }
      } catch (err) {
        console.error(err);
      } finally {
        if (!canceled) setLoading(false);
      }
    }
    
    loadAllMetrics();
    return () => { canceled = true; };
  }, [selectedId, hours]);

  const renderChart = (title, data, services, loading, unit) => (
    <div className="card card--fill" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <header>
        <h3 style={{ margin: 0 }}>{title}</h3>
      </header>
      <div className="card-body" style={{ flex: 1, minHeight: 0, padding: '16px 16px 16px 0' }}>
        {loading && data.length === 0 ? (
          <div className="empty" style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>Loading metrics...</div>
        ) : data.length === 0 ? (
          <div className="empty" style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>No data recorded in this window.</div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" vertical={false} />
              <XAxis 
                dataKey="time" 
                tickFormatter={(unixTime) => new Date(unixTime).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                stroke="var(--text-dim)" 
                fontSize={12} 
                tickMargin={8}
              />
              <YAxis stroke="var(--text-dim)" fontSize={12} tickMargin={8} tickFormatter={(val) => `${val}${unit}`} />
              <Tooltip 
                labelFormatter={(unixTime) => new Date(unixTime).toLocaleString()}
                contentStyle={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border-color)', borderRadius: '6px' }}
                itemStyle={{ fontSize: 13 }}
                formatter={(value, name) => [`${value}${unit}`, name]}
              />
              <Legend wrapperStyle={{ fontSize: 12, paddingTop: 10 }} />
              {services.map((svc, i) => (
                <Line 
                  key={svc} 
                  type="monotone" 
                  dataKey={svc} 
                  stroke={COLORS[i % COLORS.length]} 
                  strokeWidth={2} 
                  dot={false}
                  activeDot={{ r: 4 }}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="System Dashboard" style={{ width: '90vw', maxWidth: '1400px', height: '85vh', display: 'flex', flexDirection: 'column' }}>
        <header style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
          <h3>System Dashboard</h3>
          
          <select className="input input--sm" value={selectedId} onChange={(e) => setSelectedId(e.target.value)} style={{ width: '250px' }}>
            {systems.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>

          <span className="spacer" />
          
          <select value={hours} onChange={e => setHours(Number(e.target.value))} className="input input--sm" style={{ width: 'auto' }}>
            <option value={1}>Last 1 hour</option>
            <option value={6}>Last 6 hours</option>
            <option value={24}>Last 24 hours</option>
          </select>

          <button type="button" className="iconbtn" onClick={onClose} aria-label="Close" style={{ marginLeft: '16px' }}>×</button>
        </header>
        
        <div className="modal-body" style={{ flex: 1, display: 'flex', gap: '16px', overflow: 'hidden' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            {renderChart('CPU Usage', cpuData, cpuServices, loading, 'c')}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            {renderChart('RAM Usage', ramData, ramServices, loading, 'MB')}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            {renderChart('Log Count', logsData, logsServices, loading, '')}
          </div>
        </div>
      </div>
    </div>
  );
}
