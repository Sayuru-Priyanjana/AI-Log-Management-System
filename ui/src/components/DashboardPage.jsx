import React, { useState, useEffect } from 'react';
import { 
  getSystems, getSystemMetricsRequests, getSystemMetricsRam, getSystemMetricsLogs, 
  getSystemMetricsErrorLogs, getSystemMetricsRestarts, getSystemMetricsThrottling,
  getSystemAlerts, getTopErrors 
} from '../api';
import { useToast } from '../toast';
import AnomalyTimeline from './AnomalyTimeline';
import ChartModal from './ChartModal';
import MonitoringChart from './MonitoringChart';

import { useNavigate } from 'react-router-dom';

export default function DashboardPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [systems, setSystems] = useState([]);
  const [selectedId, setSelectedId] = useState(() => localStorage.getItem('lastSystemId') || null);
  const [hours, setHours] = useState(() => Number(localStorage.getItem('timeRangeHours')) || 24);
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [autoRefresh, setAutoRefresh] = useState(false);
  
  const [cpuData, setCpuData] = useState([]);
  const [ramData, setRamData] = useState([]);
  const [logsData, setLogsData] = useState([]);
  const [errorLogsData, setErrorLogsData] = useState([]);
  const [restartsData, setRestartsData] = useState([]);
  const [throttlingData, setThrottlingData] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [topErrors, setTopErrors] = useState([]);
  const [loading, setLoading] = useState(true);

  const [cpuServices, setCpuServices] = useState([]);
  const [ramServices, setRamServices] = useState([]);
  const [logsServices, setLogsServices] = useState([]);
  const [errorLogsServices, setErrorLogsServices] = useState([]);
  const [restartsServices, setRestartsServices] = useState([]);
  const [throttlingServices, setThrottlingServices] = useState([]);

  const [selectedChart, setSelectedChart] = useState(null);

  const end = Math.floor(Date.now() / 1000);
  const start = end - (hours * 3600);

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
  }, [toast, refreshTrigger]);

  useEffect(() => {
    let canceled = false;
    
    async function loadAllMetrics() {
      if (!selectedId) {
        setLoading(false);
        return;
      }
      setLoading(true);
      
      try {
        const [cpuRes, ramRes, logsRes, errorLogsRes, restartsRes, throttlingRes, alertsRes, errorsRes] = await Promise.all([
          getSystemMetricsRequests(selectedId, start, end).catch(() => []),
          getSystemMetricsRam(selectedId, start, end).catch(() => []),
          getSystemMetricsLogs(selectedId, start, end).catch(() => []),
          getSystemMetricsErrorLogs(selectedId, start, end).catch(() => []),
          getSystemMetricsRestarts(selectedId, start, end).catch(() => []),
          getSystemMetricsThrottling(selectedId, start, end).catch(() => []),
          getSystemAlerts(selectedId).catch(() => []),
          getTopErrors(selectedId, start, end).catch(() => [])
        ]);
        
        if (!canceled) {
          setCpuData(cpuRes);
          setRamData(ramRes);
          setLogsData(logsRes);
          setErrorLogsData(errorLogsRes);
          setRestartsData(restartsRes);
          setThrottlingData(throttlingRes);
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
          setErrorLogsServices(extractServices(errorLogsRes));
          setRestartsServices(extractServices(restartsRes));
          setThrottlingServices(extractServices(throttlingRes));
        }
      } catch (err) {
        console.error(err);
      } finally {
        if (!canceled) setLoading(false);
      }
    }
    
    loadAllMetrics();
    return () => { canceled = true; };
  }, [selectedId, hours, refreshTrigger]);

  useEffect(() => {
    let interval;
    if (autoRefresh) {
      interval = setInterval(() => {
        setRefreshTrigger(prev => prev + 1);
      }, 10000);
    }
    return () => clearInterval(interval);
  }, [autoRefresh]);

  const selectSystem = (id) => {
    setSelectedId(id);
    localStorage.setItem('lastSystemId', id);
  };




  return (
    <div className="wsx" style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 46px)' }}>
      <div className="wsx-top">
        <select className="input" value={selectedId || ''} disabled={systems.length === 0}
          onChange={(e) => selectSystem(e.target.value)}>
          {systems.length === 0 && <option value="">No systems discovered yet</option>}
          {systems.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        
        <span className="spacer" />
        
        <div style={{ display: 'flex', gap: '20px', alignItems: 'center' }}>
          <select value={hours} onChange={e => {
            const h = Number(e.target.value);
            setHours(h);
            localStorage.setItem('timeRangeHours', h);
          }} className="input input--sm" style={{ width: 'auto', padding: '2px 8px' }}>
            <option value={1}>Last 1 hour</option>
            <option value={6}>Last 6 hours</option>
            <option value={24}>Last 24 hours</option>
            <option value={168}>Last 7 days</option>
          </select>
          
          <button type="button" className="btn btn--sm" onClick={() => setRefreshTrigger(prev => prev + 1)}>
            Refresh
          </button>
          
          <button 
            type="button" 
            className={`btn btn--sm ${autoRefresh ? 'btn--primary' : ''}`}
            onClick={() => setAutoRefresh(!autoRefresh)}
            style={{ display: 'flex', alignItems: 'center', gap: '5px' }}
          >
            {autoRefresh && <span className="dot dot--ok" style={{ animation: 'toast-in 1s infinite alternate' }} />}
            Auto Reload {autoRefresh ? 'ON' : 'OFF'}
          </button>
          
          <button onClick={() => navigate('/logs')} className="btn btn--primary btn--sm" style={{ padding: '2px 12px', fontWeight: 'bold' }}>
            Go to Logs →
          </button>
        </div>
      </div>

      {!selectedId ? (
        <div className="empty-state" style={{ marginTop: 40 }}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <polygon points="12 2 2 7 12 12 22 7 12 2"></polygon>
            <polyline points="2 17 12 22 22 17"></polyline>
            <polyline points="2 12 12 17 22 12"></polyline>
          </svg>
          <div className="empty-state-title">{loading ? 'Loading...' : 'No systems found'}</div>
          <div className="empty-state-desc">{loading ? 'Discovering available systems.' : 'No systems have shipped logs yet.'}</div>
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
                start={start} 
                end={end} 
              />
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '20px', flexShrink: 0 }}>
            <div style={{ minHeight: '400px', display: 'flex' }}>
              <div style={{ flex: 1 }}>
                <MonitoringChart 
                  title="CPU Usage (cores)" data={cpuData} services={cpuServices} loading={loading} unit="c" showControls={false} showLegend={false} defaultTopN={5}
                  onClick={() => setSelectedChart({ title: 'CPU Usage (cores)', data: cpuData, services: cpuServices, unit: 'c', fetchFn: (s, e) => getSystemMetricsRequests(selectedId, s, e) })} 
                />
              </div>
            </div>
            <div style={{ minHeight: '400px', display: 'flex' }}>
              <div style={{ flex: 1 }}>
                <MonitoringChart 
                  title="RAM Usage" data={ramData} services={ramServices} loading={loading} unit="MB" showControls={false} showLegend={false} defaultTopN={5}
                  onClick={() => setSelectedChart({ title: 'RAM Usage', data: ramData, services: ramServices, unit: 'MB', fetchFn: (s, e) => getSystemMetricsRam(selectedId, s, e) })} 
                />
              </div>
            </div>
            <div style={{ minHeight: '400px', display: 'flex' }}>
              <div style={{ flex: 1 }}>
                <MonitoringChart 
                  title="Log Ingestion Rate" data={logsData} services={logsServices} loading={loading} unit="" showControls={false} showLegend={false} defaultTopN={5}
                  onClick={() => setSelectedChart({ title: 'Log Ingestion Rate', data: logsData, services: logsServices, unit: '', fetchFn: (s, e) => getSystemMetricsLogs(selectedId, s, e) })} 
                />
              </div>
            </div>
            <div style={{ minHeight: '400px', display: 'flex' }}>
              <div style={{ flex: 1 }}>
                <MonitoringChart 
                  title="Error Log Rate" data={errorLogsData} services={errorLogsServices} loading={loading} unit="" showControls={false} showLegend={false} defaultTopN={5}
                  emptyMessage="No error logs recorded"
                  onClick={() => setSelectedChart({ title: 'Error Log Rate', data: errorLogsData, services: errorLogsServices, unit: '', fetchFn: (s, e) => getSystemMetricsErrorLogs(selectedId, s, e) })} 
                />
              </div>
            </div>
            <div style={{ minHeight: '400px', display: 'flex' }}>
              <div style={{ flex: 1 }}>
                <MonitoringChart 
                  title="Container Restarts" data={restartsData} services={restartsServices} loading={loading} unit="" showControls={false} showLegend={false} defaultTopN={5}
                  emptyMessage="No container restarts recorded"
                  onClick={() => setSelectedChart({ title: 'Container Restarts', data: restartsData, services: restartsServices, unit: '', fetchFn: (s, e) => getSystemMetricsRestarts(selectedId, s, e) })} 
                />
              </div>
            </div>
            <div style={{ minHeight: '400px', display: 'flex' }}>
              <div style={{ flex: 1 }}>
                <MonitoringChart 
                  title="CPU Throttling" data={throttlingData} services={throttlingServices} loading={loading} unit="%" showControls={false} showLegend={false} defaultTopN={5}
                  emptyMessage="No CPU throttling recorded"
                  onClick={() => setSelectedChart({ title: 'CPU Throttling', data: throttlingData, services: throttlingServices, unit: '%', fetchFn: (s, e) => getSystemMetricsThrottling(selectedId, s, e) })} 
                />
              </div>
            </div>
          </div>

          <div className="card card--fill" style={{ padding: '0', overflow: 'visible', flexShrink: 0 }}>
            <header style={{ padding: '12px 16px', background: 'transparent', borderBottom: '1px solid var(--border)' }}>
              <h3 style={{ margin: 0, color: 'var(--text-2)', fontSize: '13px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px' }}>Top Error Patterns</h3>
            </header>
            <div style={{ padding: '16px' }}>
            {loading ? (
              <div className="empty-state">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 12a9 9 0 1 1-6.219-8.56"></path>
                </svg>
                <div className="empty-state-title">Loading errors</div>
                <div className="empty-state-desc">Please wait while data is retrieved...</div>
              </div>
            ) : topErrors.length === 0 ? (
              <div className="empty-state">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="10"></circle>
                  <line x1="12" y1="8" x2="12" y2="12"></line>
                  <line x1="12" y1="16" x2="12.01" y2="16"></line>
                </svg>
                <div className="empty-state-title">No errors detected</div>
                <div className="empty-state-desc">No errors were found in this time window.</div>
              </div>
            ) : (
              <table className="table" style={{ width: '100%', textAlign: 'left', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)' }}>
                    <th style={{ padding: '8px', color: 'var(--text-3)', fontWeight: 600 }}>Error Message</th>
                    <th style={{ padding: '8px', color: 'var(--text-3)', fontWeight: 600, width: '200px' }}>Service</th>
                    <th style={{ padding: '8px', color: 'var(--text-3)', fontWeight: 600, width: '100px', textAlign: 'right' }}>Occurrences</th>
                  </tr>
                </thead>
                <tbody>
                  {topErrors.map((err, idx) => (
                    <tr key={idx} style={{ borderBottom: '1px solid var(--border-soft)' }}>
                      <td style={{ padding: '8px', color: 'var(--err)', fontFamily: 'var(--mono)', fontSize: '13px' }}>{err.message}</td>
                      <td style={{ padding: '8px', color: 'var(--text-2)', fontSize: '13px' }}>{err.service || 'Unknown'}</td>
                      <td style={{ padding: '8px', textAlign: 'right', fontWeight: 600 }}>{err.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            </div>
          </div>
        </div>
      )}
      
      {selectedChart && (
        <ChartModal 
          isOpen={true}
          onClose={() => setSelectedChart(null)}
          title={selectedChart.title}
          currentData={selectedChart.data}
          services={selectedChart.services}
          unit={selectedChart.unit}
          isStacked={selectedChart.isStacked}
          fetchComparisonData={selectedChart.fetchFn}
          currentStart={start}
          currentEnd={end}
        />
      )}
    </div>
  );
}
