import React, { useState, useEffect } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer
} from 'recharts';
import { getSystems, getSystemMetricsRequests, getSystemMetricsRam, getSystemMetricsLogs, getSystemAlerts, getTopErrors } from '../api';
import { useToast } from '../toast';
import AnomalyTimeline from './AnomalyTimeline';
import LogExplorer from './LogExplorer';

const COLORS = [
  '#2E72D2', '#00A78F', '#632CA6', '#E24D42', '#F39B00',
  '#00875A', '#D9B300', '#D34836', '#9B59B6', '#34495E'
];

const CustomTooltip = ({ active, payload, label, unit }) => {
  if (active && payload && payload.length) {
    return (
      <div className="chart-tooltip">
        <div className="chart-tooltip-header">
          {new Date(label).toLocaleString()}
        </div>
        <div className="chart-tooltip-body">
          {payload.map((entry, index) => (
            <div key={index} className="chart-tooltip-item">
              <span className="chart-tooltip-color" style={{ backgroundColor: entry.color }}></span>
              <span className="chart-tooltip-name">{entry.name}</span>
              <span className="chart-tooltip-value">{entry.value}{unit}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }
  return null;
};

export default function DashboardPage() {
  const toast = useToast();
  const [systems, setSystems] = useState([]);
  const [selectedId, setSelectedId] = useState(() => localStorage.getItem('lastSystemId') || null);
  const [hours, setHours] = useState(24);
  
  const [cpuData, setCpuData] = useState([]);
  const [ramData, setRamData] = useState([]);
  const [logsData, setLogsData] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [topErrors, setTopErrors] = useState([]);
  const [loading, setLoading] = useState(true);

  const [cpuServices, setCpuServices] = useState([]);
  const [ramServices, setRamServices] = useState([]);
  const [logsServices, setLogsServices] = useState([]);

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

  useEffect(() => {
    let canceled = false;
    
    async function loadAllMetrics() {
      if (!selectedId) {
        setLoading(false);
        return;
      }
      setLoading(true);
      
      const end = Math.floor(Date.now() / 1000);
      const start = end - (hours * 3600);
      
      try {
        const [cpuRes, ramRes, logsRes, alertsRes, errorsRes] = await Promise.all([
          getSystemMetricsRequests(selectedId, start, end).catch(() => []),
          getSystemMetricsRam(selectedId, start, end).catch(() => []),
          getSystemMetricsLogs(selectedId, start, end).catch(() => []),
          getSystemAlerts(selectedId).catch(() => []),
          getTopErrors(selectedId, start, end).catch(() => [])
        ]);
        
        if (!canceled) {
          setCpuData(cpuRes);
          setRamData(ramRes);
          setLogsData(logsRes);
          setTopErrors(errorsRes || []);
          
          setAlerts(alertsRes || []);
          
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

  const selectSystem = (id) => {
    setSelectedId(id);
    localStorage.setItem('lastSystemId', id);
  };

  const renderChart = (title, data, services, loading, unit) => (
    <div className="card card--fill" style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '0', overflow: 'visible' }}>
      <header style={{ padding: '12px 16px', background: 'transparent', borderBottom: '1px solid var(--border)' }}>
        <h3 style={{ margin: 0, color: 'var(--text-2)', fontSize: '13px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px' }}>{title}</h3>
      </header>
      <div className="card-body" style={{ flex: 1, minHeight: 0, padding: '16px 24px 16px 0', overflow: 'visible' }}>
        {loading && data.length === 0 ? (
          <div className="empty" style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>Loading metrics...</div>
        ) : data.length === 0 ? (
          <div className="empty" style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>No data recorded in this window.</div>
        ) : (
          <ResponsiveContainer width="100%" height="100%" style={{ overflow: 'visible' }}>
            <AreaChart data={data} margin={{ top: 10, right: 10, left: 0, bottom: 0 }} style={{ overflow: 'visible' }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
              <XAxis 
                dataKey="time" 
                tickFormatter={(unixTime) => new Date(unixTime).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                stroke="var(--text-3)" 
                fontSize={11} 
                tickMargin={12}
                axisLine={false}
                tickLine={false}
              />
              <YAxis 
                stroke="var(--text-3)" 
                fontSize={11} 
                tickMargin={12} 
                tickFormatter={(val) => `${val}${unit}`}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip 
                content={<CustomTooltip unit={unit} />}
                cursor={{ stroke: 'var(--text-2)', strokeWidth: 1, strokeDasharray: '3 3' }}
                isAnimationActive={false}
              />
              <defs>
                {services.map((svc, i) => (
                  <linearGradient key={svc} id={`grad-${i}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={COLORS[i % COLORS.length]} stopOpacity={0.8}/>
                    <stop offset="95%" stopColor={COLORS[i % COLORS.length]} stopOpacity={0.2}/>
                  </linearGradient>
                ))}
              </defs>
              {services.map((svc, i) => {
                const color = COLORS[i % COLORS.length];
                return (
                  <Area 
                    key={svc} 
                    type="monotone" 
                    dataKey={svc} 
                    stackId="1"
                    stroke={color}
                    fill={`url(#grad-${i})`}
                    strokeWidth={2}
                    fillOpacity={1}
                    isAnimationActive={true}
                    animationDuration={1000}
                  />
                );
              })}
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );

  return (
    <div className="wsx" style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 46px)' }}>
      <div className="wsx-top">
        <select className="input" value={selectedId || ''} disabled={systems.length === 0}
          onChange={(e) => selectSystem(e.target.value)}>
          {systems.length === 0 && <option value="">No systems discovered yet</option>}
          {systems.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        
        <span className="spacer" />
        
        <select value={hours} onChange={e => setHours(Number(e.target.value))} className="input input--sm" style={{ width: 'auto', padding: '2px 8px' }}>
          <option value={1}>Last 1 hour</option>
          <option value={6}>Last 6 hours</option>
          <option value={24}>Last 24 hours</option>
        </select>
      </div>

      {!selectedId ? (
        <div className="empty" style={{ marginTop: 40 }}>
          {loading ? 'Loading…' : 'No systems have shipped logs yet.'}
        </div>
      ) : (
        <div style={{ flex: 1, padding: '20px', display: 'flex', flexDirection: 'column', gap: '20px', overflowY: 'auto', minHeight: 0 }}>
          <div className="card card--fill" style={{ padding: '0', flexShrink: 0, overflow: 'visible' }}>
            <header style={{ padding: '12px 16px', background: 'transparent', borderBottom: '1px solid var(--border)' }}>
              <h3 style={{ margin: 0, color: 'var(--text-2)', fontSize: '13px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px' }}>Detections & Anomalies</h3>
            </header>
            <div style={{ padding: '16px' }}>
              <AnomalyTimeline 
                alerts={alerts} 
                start={Math.floor(Date.now() / 1000) - (hours * 3600)} 
                end={Math.floor(Date.now() / 1000)} 
              />
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(400px, 1fr))', gap: '20px', flexShrink: 0 }}>
            <div style={{ minHeight: '300px', display: 'flex' }}>
              <div style={{ flex: 1 }}>{renderChart('CPU Usage (cores)', cpuData, cpuServices, loading, 'c')}</div>
            </div>
            <div style={{ minHeight: '300px', display: 'flex' }}>
              <div style={{ flex: 1 }}>{renderChart('RAM Usage', ramData, ramServices, loading, 'MB')}</div>
            </div>
            <div style={{ minHeight: '300px', display: 'flex' }}>
              <div style={{ flex: 1 }}>{renderChart('Log Ingestion Rate', logsData, logsServices, loading, '')}</div>
            </div>
          </div>

          <div className="card card--fill" style={{ padding: '0', overflow: 'visible', flexShrink: 0 }}>
            <header style={{ padding: '12px 16px', background: 'transparent', borderBottom: '1px solid var(--border)' }}>
              <h3 style={{ margin: 0, color: 'var(--text-2)', fontSize: '13px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px' }}>Top Error Patterns</h3>
            </header>
            <div style={{ padding: '16px' }}>
            {loading ? (
              <div className="empty">Loading errors...</div>
            ) : topErrors.length === 0 ? (
              <div className="empty">No errors detected in this window.</div>
            ) : (
              <table className="table" style={{ width: '100%', textAlign: 'left', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)' }}>
                    <th style={{ padding: '8px', color: 'var(--text-3)', fontWeight: 600 }}>Error Message</th>
                    <th style={{ padding: '8px', color: 'var(--text-3)', fontWeight: 600, width: '100px', textAlign: 'right' }}>Occurrences</th>
                  </tr>
                </thead>
                <tbody>
                  {topErrors.map((err, idx) => (
                    <tr key={idx} style={{ borderBottom: '1px solid var(--border-soft)' }}>
                      <td style={{ padding: '8px', color: 'var(--err)', fontFamily: 'var(--mono)', fontSize: '13px' }}>{err.message}</td>
                      <td style={{ padding: '8px', textAlign: 'right', fontWeight: 600 }}>{err.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            </div>
          </div>

          <LogExplorer systemId={selectedId} services={logsServices} />
        </div>
      )}
    </div>
  );
}
