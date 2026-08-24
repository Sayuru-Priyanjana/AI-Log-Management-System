import React, { useState, useMemo } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceArea
} from 'recharts';

const GRAFANA_COLORS = [
  '#7EB26D', '#EAB839', '#6ED0E0', '#EF843C', '#E24D42', '#1F78C1', '#BA43A9', '#705DA0',
  '#508642', '#CCA300', '#447EBC', '#C15C17', '#890F02', '#0A437C', '#6D1F62', '#584477',
  '#B7DBAB', '#F4D598', '#70DBED', '#F9BA8F', '#F29191', '#82B5D8', '#E5A8E2', '#AEA2E0',
  '#629E51', '#E5AC0E', '#64B0C8', '#E0752D', '#BF1B00', '#0A50A1', '#962D82', '#614D93',
  '#9AC48A', '#F2C96D', '#65C5DB', '#F9934E', '#EA6460', '#5195CE', '#D683CE', '#806EB7',
  '#3F6833', '#967302', '#2F575E', '#99440A', '#58140C', '#052B51', '#511749', '#3F2B5B',
  '#E0F9D7', '#FCEACA', '#CFFAFF', '#F9E2D2', '#FCE2DE', '#BADFF4', '#F9D9F9', '#DEDAF7'
];

// Stable color hashing using Grafana palette
const stringToColor = (str) => {
  if (str === 'Other') return '#6b7280'; // Neutral grey for the Other category
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  }
  const index = Math.abs(hash) % GRAFANA_COLORS.length;
  return GRAFANA_COLORS[index];
};


const CustomTooltip = ({ active, payload, label, unit }) => {
  if (active && payload && payload.length) {
    // Sort payload by value descending
    const sortedPayload = [...payload].sort((a, b) => b.value - a.value);
    
    return (
      <div className="chart-tooltip">
        <div className="chart-tooltip-header">
          {new Date(label).toLocaleString()}
        </div>
        <div className="chart-tooltip-body">
          {sortedPayload.map((entry, index) => (
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

export default function MonitoringChart({ title, data, services, compareServices = [], loading, unit, onClick, showControls = true, showLegend = true, defaultTopN = 10 }) {
  const [hiddenSeries, setHiddenSeries] = useState(new Set());
  const [topN, setTopN] = useState(defaultTopN);
  
  const safeData = data || [];

  // Zoom state
  const [refAreaLeft, setRefAreaLeft] = useState('');
  const [refAreaRight, setRefAreaRight] = useState('');
  const [left, setLeft] = useState('dataMin');
  const [right, setRight] = useState('dataMax');

  // Calculate top N services and process data to include "Other" category
  const { visibleServices, processedData } = useMemo(() => {
    if (safeData.length === 0) return { visibleServices: services, processedData: safeData };
    
    // Find max values across all time for sorting
    const maxValues = {};
    services.forEach(s => maxValues[s] = 0);
    
    safeData.forEach(point => {
      services.forEach(s => {
        if (point[s] > maxValues[s]) {
          maxValues[s] = point[s];
        }
      });
    });
    
    const sortedServices = Object.keys(maxValues).sort((a, b) => maxValues[b] - maxValues[a]);
    
    if (topN === 'all') {
      return { visibleServices: sortedServices, processedData: safeData };
    }
    
    const topServices = sortedServices.slice(0, topN);
    const otherServices = sortedServices.slice(topN);
    
    if (otherServices.length === 0) {
      return { visibleServices: topServices, processedData: safeData };
    }
    
    // Sum the remaining services into the "Other" category for each data point
    const newProcessedData = safeData.map(point => {
      const newPoint = { ...point };
      let otherSum = 0;
      otherServices.forEach(s => {
        if (newPoint[s] !== undefined) {
          otherSum += newPoint[s];
        }
      });
      newPoint['Other'] = otherSum;
      return newPoint;
    });
    
    return {
      visibleServices: [...topServices, 'Other'],
      processedData: newProcessedData
    };
  }, [data, services, topN]);

  const toggleSeries = (seriesName) => {
    setHiddenSeries(prev => {
      // If ONLY this series is currently visible, clicking it resets to show ALL
      if (prev.size === visibleServices.length - 1 && !prev.has(seriesName)) {
        return new Set();
      }
      
      // Otherwise, isolate this series by hiding all other services
      const next = new Set(visibleServices);
      next.delete(seriesName);
      return next;
    });
  };

  const zoom = () => {
    let _refAreaLeft = refAreaLeft;
    let _refAreaRight = refAreaRight;

    if (_refAreaLeft === _refAreaRight || _refAreaRight === '') {
      setRefAreaLeft('');
      setRefAreaRight('');
      return;
    }

    // Ensure left is before right
    if (_refAreaLeft > _refAreaRight) {
      [ _refAreaLeft, _refAreaRight ] = [ _refAreaRight, _refAreaLeft ];
    }

    setRefAreaLeft('');
    setRefAreaRight('');
    setLeft(_refAreaLeft);
    setRight(_refAreaRight);
  };

  const zoomOut = (e) => {
    e.stopPropagation();
    setLeft('dataMin');
    setRight('dataMax');
  };

  const handleProgrammaticZoom = (e, zoomIn = true) => {
    e.stopPropagation();
    if (!processedData || processedData.length === 0) return;
    
    let currentLeft = left;
    let currentRight = right;
    
    const actualDataMin = Math.min(...processedData.map(d => d.time));
    const actualDataMax = Math.max(...processedData.map(d => d.time));

    if (currentLeft === 'dataMin' || currentRight === 'dataMax') {
      currentLeft = actualDataMin;
      currentRight = actualDataMax;
    }

    const duration = currentRight - currentLeft;
    // Zoom by 20% (10% on each side)
    const zoomAmount = zoomIn ? duration * 0.1 : -(duration * 0.1); 
    
    let newLeft = currentLeft + zoomAmount;
    let newRight = currentRight - zoomAmount;

    // Prevent zooming in too far (e.g., less than 5 seconds)
    if (zoomIn && newRight - newLeft < 5000) return;

    if (!zoomIn) {
      if (newLeft < actualDataMin) newLeft = actualDataMin;
      if (newRight > actualDataMax) newRight = actualDataMax;
      
      // If we zoomed all the way out, reset to auto bounds
      if (newLeft <= actualDataMin && newRight >= actualDataMax) {
        setLeft('dataMin');
        setRight('dataMax');
        return;
      }
    }

    setLeft(newLeft);
    setRight(newRight);
  };


  return (
    <div 
      className="card card--fill chart-card" 
      style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '0', cursor: onClick ? 'pointer' : 'default' }}
      onClick={(e) => {
        if (e.target.closest('.chart-controls') || e.target.closest('.custom-legend')) return;
        if (onClick) onClick();
      }}
      title={onClick ? "Click to open full comparison view" : ""}
    >
      <header style={{ padding: '12px 16px', background: 'transparent', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }} className="chart-controls">
        <h3 style={{ margin: 0, color: 'var(--text-2)', fontSize: '13px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px' }}>{title}</h3>
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
          {showControls && (
            <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
              <button className="btn btn--sm" onClick={(e) => handleProgrammaticZoom(e, true)} style={{ padding: '2px 8px', fontSize: '11px', fontWeight: 'bold' }} title="Zoom In">
                +
              </button>
              <button className="btn btn--sm" onClick={(e) => handleProgrammaticZoom(e, false)} style={{ padding: '2px 8px', fontSize: '11px', fontWeight: 'bold' }} title="Zoom Out">
                -
              </button>
            </div>
          )}
          {left !== 'dataMin' && (
            <button className="btn btn--sm" onClick={zoomOut} style={{ padding: '2px 8px', fontSize: '11px' }}>
              Reset Zoom
            </button>
          )}
          {showControls && (
            <select 
              className="input input--sm" 
              style={{ width: 'auto', padding: '2px 8px', fontSize: '11px', backgroundColor: 'var(--surface)' }}
              value={topN}
              onChange={(e) => setTopN(e.target.value === 'all' ? 'all' : Number(e.target.value))}
              onClick={(e) => e.stopPropagation()}
            >
              <option value={5}>Top 5</option>
              <option value={10}>Top 10</option>
              <option value={20}>Top 20</option>
              <option value="all">All</option>
            </select>
          )}
          {onClick && !showControls && (
            <button 
              className="btn btn--sm" 
              onClick={(e) => { e.stopPropagation(); onClick(); }} 
              style={{ padding: '4px', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'transparent', border: '1px solid var(--border)' }}
              title="Expand Chart"
            >
              <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="15 3 21 3 21 9"></polyline>
                <polyline points="9 21 3 21 3 15"></polyline>
                <line x1="21" y1="3" x2="14" y2="10"></line>
                <line x1="3" y1="21" x2="10" y2="14"></line>
              </svg>
            </button>
          )}
        </div>
      </header>
      
      <div className="card-body" style={{ flex: 1, display: 'flex', flexDirection: 'row', minHeight: 0, padding: '16px 24px 16px 0' }}>
        {loading && safeData.length === 0 ? (
          <div className="empty-state" style={{ height: '100%', width: '100%' }}>
            <div className="empty-state-title">Loading metrics...</div>
          </div>
        ) : safeData.length === 0 ? (
          <div className="empty-state" style={{ height: '100%', width: '100%' }}>
            <div className="empty-state-title">No metrics recorded</div>
          </div>
        ) : (
          <>
            <div style={{ flex: 1, minWidth: 0, minHeight: 0 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart 
                  data={processedData} 
                  margin={{ top: 10, right: 10, left: 0, bottom: 0 }}
                  onMouseDown={(e) => { if(e) setRefAreaLeft(e.activeLabel) }}
                  onMouseMove={(e) => { if(e && refAreaLeft) setRefAreaRight(e.activeLabel) }}
                  onMouseUp={zoom}
                >
                  <defs>
                    {visibleServices.map(svc => {
                      const color = stringToColor(svc);
                      const id = `color-${svc.replace(/[^a-zA-Z0-9]/g, '')}`;
                      return (
                        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1" key={id}>
                          <stop offset="5%" stopColor={color} stopOpacity={0.8}/>
                          <stop offset="95%" stopColor={color} stopOpacity={0.1}/>
                        </linearGradient>
                      );
                    })}
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
                  <XAxis 
                    allowDataOverflow 
                    dataKey="time" 
                    domain={[left, right]} 
                    type="number"
                    tickFormatter={(unixTime) => new Date(unixTime).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    stroke="var(--text-3)" 
                    fontSize={11} 
                    tickMargin={12}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis 
                    allowDataOverflow 
                    stroke="var(--text-3)" 
                    fontSize={11} 
                    tickMargin={12} 
                    tickFormatter={(val) => {
                      if (val >= 1000000) return (val / 1000000).toFixed(1) + 'M';
                      if (val >= 1000) return (val / 1000).toFixed(1) + 'k';
                      return val + unit;
                    }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <Tooltip 
                    content={<CustomTooltip unit={unit} />}
                    cursor={{ stroke: 'var(--text-2)', strokeWidth: 1, strokeDasharray: '3 3' }}
                    isAnimationActive={false}
                  />
                  {visibleServices.map((svc) => (
                    <Area 
                      key={svc} 
                      type="monotone" 
                      dataKey={svc} 
                      stackId="1"
                      stroke={stringToColor(svc)}
                      fill={`url(#color-${svc.replace(/[^a-zA-Z0-9]/g, '')})`}
                      fillOpacity={1}
                      strokeWidth={2}
                      hide={hiddenSeries.has(svc)}
                      isAnimationActive={false}
                    />
                  ))}
                  {compareServices.map((svc) => {
                    const originalSvc = svc.replace('_compare', '');
                    return (
                      <Area 
                        key={svc} 
                        type="monotone" 
                        dataKey={svc} 
                        stackId="2"
                        stroke={stringToColor(originalSvc)}
                        strokeDasharray="5 5"
                        fill="transparent"
                        strokeWidth={2}
                        hide={hiddenSeries.has(originalSvc)}
                        isAnimationActive={false}
                      />
                    );
                  })}
                  
                  {refAreaLeft && refAreaRight ? (
                    <ReferenceArea x1={refAreaLeft} x2={refAreaRight} strokeOpacity={0.3} fill="var(--primary)" />
                  ) : null}
                </AreaChart>
              </ResponsiveContainer>
            </div>
            
            {showLegend && (
              <div className="custom-legend" style={{ width: '180px', flexShrink: 0, paddingLeft: '16px', borderLeft: '1px solid var(--border)', overflowY: 'auto' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {visibleServices.map((svc) => {
                    const isHidden = hiddenSeries.has(svc);
                    const color = stringToColor(svc);
                    return (
                      <div 
                        key={svc} 
                        onClick={() => toggleSeries(svc)}
                        title={`${svc}\n(Click to isolate)`}
                        style={{ 
                          display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer', 
                          fontSize: '11px', opacity: isHidden ? 0.4 : 1, transition: 'opacity 0.2s',
                          color: 'var(--text-2)', overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis'
                        }}
                      >
                        <span style={{ minWidth: '10px', height: '10px', borderRadius: '50%', backgroundColor: color }}></span>
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{svc}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
