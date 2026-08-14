import React, { useState, useEffect } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer
} from 'recharts';
import { getSystemMetricsRequests } from '../api';

const COLORS = [
  '#0ea5e9', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
  '#ec4899', '#f97316', '#14b8a6', '#6366f1', '#84cc16'
];

export function MetricsPanel({ systemId }) {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(false);
  const [hours, setHours] = useState(24);
  const [services, setServices] = useState([]);

  useEffect(() => {
    let canceled = false;
    
    async function loadMetrics() {
      if (!systemId) return;
      setLoading(true);
      
      const end = Math.floor(Date.now() / 1000);
      const start = end - (hours * 3600);
      
      try {
        const json = await getSystemMetricsRequests(systemId, start, end);
        
        if (!canceled) {
          setData(json);
          
          const svcSet = new Set();
          json.forEach(point => {
            Object.keys(point).forEach(k => {
              if (k !== 'time') svcSet.add(k);
            });
          });
          setServices(Array.from(svcSet));
        }
      } catch (err) {
        console.error(err);
      } finally {
        if (!canceled) setLoading(false);
      }
    }
    
    loadMetrics();
    return () => { canceled = true; };
  }, [systemId, hours]);

  return (
    <div className="card card--fill" style={{ display: 'flex', flexDirection: 'column' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 style={{ margin: 0 }}>CPU Usage (cores)</h3>
        <select value={hours} onChange={e => setHours(Number(e.target.value))} className="input input--sm" style={{ width: 'auto', padding: '2px 8px' }}>
          <option value={1}>Last 1 hour</option>
          <option value={6}>Last 6 hours</option>
          <option value={24}>Last 24 hours</option>
        </select>
      </header>
      <div className="card-body" style={{ flex: 1, minHeight: 0, padding: '16px 16px 16px 0' }}>
        {loading && data.length === 0 ? (
          <div className="empty" style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>Loading metrics...</div>
        ) : data.length === 0 ? (
          <div className="empty" style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>No requests recorded in this window.</div>
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
              <YAxis stroke="var(--text-dim)" fontSize={12} tickMargin={8} />
              <Tooltip 
                labelFormatter={(unixTime) => new Date(unixTime).toLocaleString()}
                contentStyle={{ backgroundColor: 'var(--bg-elevated)', borderColor: 'var(--border-color)', borderRadius: '6px' }}
                itemStyle={{ fontSize: 13 }}
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
}
