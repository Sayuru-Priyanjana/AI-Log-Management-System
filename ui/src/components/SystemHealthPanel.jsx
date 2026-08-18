import React, { useEffect, useState } from 'react';
import { getSystemSnapshot } from '../api';

export default function SystemHealthPanel({ systemId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let canceled = false;
    setLoading(true);
    getSystemSnapshot(systemId)
      .then((res) => {
        if (!canceled) {
          setData(res);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!canceled) setLoading(false);
      });
    return () => { canceled = true; };
  }, [systemId]);

  if (loading) {
    return (
      <div className="card">
        <header><h3>System Health Overview</h3></header>
        <div className="card-body empty">Loading health snapshot...</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="card">
        <header><h3>System Health Overview</h3></header>
        <div className="card-body empty">Could not load snapshot.</div>
      </div>
    );
  }

  const uptimeDays = Math.floor((Date.now() / 1000 - data.first_seen) / 86400);

  return (
    <div className="card">
      <header>
        <h3>System Health Overview</h3>
      </header>
      <div className="card-body">
        <dl className="kv-list">
          <div className="kv-row">
            <dt>Total Logs (24h)</dt>
            <dd className="num" style={{ fontWeight: 600, color: 'var(--accent)' }}>
              {data.total_logs_24h.toLocaleString()}
            </dd>
          </div>
          <div className="kv-row">
            <dt>First Seen</dt>
            <dd>{uptimeDays > 0 ? `${uptimeDays} days ago` : 'Today'}</dd>
          </div>
          <div className="kv-row">
            <dt>Top CPU Consumer</dt>
            <dd>
              {data.top_cpu_service ? (
                <span>
                  <span className="mono" style={{ marginRight: '8px' }}>{data.top_cpu_service}</span>
                  <span className="dim">({data.top_cpu_value}c)</span>
                </span>
              ) : (
                <span className="dim">Unknown</span>
              )}
            </dd>
          </div>
          <div className="kv-row">
            <dt>Top RAM Consumer</dt>
            <dd>
              {data.top_ram_service ? (
                <span>
                  <span className="mono" style={{ marginRight: '8px' }}>{data.top_ram_service}</span>
                  <span className="dim">({data.top_ram_value}MB)</span>
                </span>
              ) : (
                <span className="dim">Unknown</span>
              )}
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}
