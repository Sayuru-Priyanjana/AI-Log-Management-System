import React, { useState, useRef, useEffect } from 'react';

export default function TimeframePicker({ onChange, defaultLabel = 'Last 24 hours' }) {
  const initialEnd = Math.floor(Date.now() / 1000);
  const initialStart = initialEnd - (24 * 3600);
  
  const [currentStart, setCurrentStart] = useState(initialStart);
  const [currentEnd, setCurrentEnd] = useState(initialEnd);
  const [isOpen, setIsOpen] = useState(false);
  const [currentLabel, setCurrentLabel] = useState(defaultLabel);
  const [quickNum, setQuickNum] = useState('15');
  const [quickUnit, setQuickUnit] = useState('minutes');
  const popoverRef = useRef(null);

  // Close popover when clicking outside
  useEffect(() => {
    function handleClickOutside(event) {
      if (popoverRef.current && !popoverRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  const applyTimeframe = (label, startUnix, endUnix, close = true, isRelative = false, relativeType = null) => {
    setCurrentLabel(label);
    setCurrentStart(startUnix);
    setCurrentEnd(endUnix);
    if (close) setIsOpen(false);
    onChange({ start: startUnix, end: endUnix, label, isRelative, relativeType });
  };

  const handleStepBack = () => {
    const windowSize = currentEnd - currentStart;
    const newStart = currentStart - windowSize;
    const newEnd = currentEnd - windowSize;
    const label = `${new Date(newStart * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })} to ${new Date(newEnd * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}`;
    applyTimeframe(label, newStart, newEnd, false, false);
  };

  const handleStepForward = () => {
    const windowSize = currentEnd - currentStart;
    const newStart = currentStart + windowSize;
    const newEnd = currentEnd + windowSize;
    const label = `${new Date(newStart * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })} to ${new Date(newEnd * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}`;
    applyTimeframe(label, newStart, newEnd, false, false);
  };

  const handleCommonClick = (label, seconds) => {
    const end = Math.floor(Date.now() / 1000);
    const start = end - seconds;
    applyTimeframe(label, start, end, true, true, { type: 'seconds', value: seconds });
  };

  const handleToday = () => {
    const now = new Date();
    const end = Math.floor(now.getTime() / 1000);
    const start = Math.floor(new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000);
    applyTimeframe('Today', start, end, true, true, { type: 'today' });
  };

  const handleThisWeek = () => {
    const now = new Date();
    const end = Math.floor(now.getTime() / 1000);
    const day = now.getDay() || 7; // Get current day number, converting Sun. to 7
    if (day !== 1) now.setHours(-24 * (day - 1)); // Set to Monday
    const start = Math.floor(new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000);
    applyTimeframe('This week', start, end, true, true, { type: 'thisWeek' });
  };

  const handleQuickApply = () => {
    const num = parseInt(quickNum, 10);
    if (isNaN(num) || num <= 0) return;
    let multiplier = 60; // minutes
    if (quickUnit === 'hours') multiplier = 3600;
    if (quickUnit === 'days') multiplier = 86400;
    
    const seconds = num * multiplier;
    handleCommonClick(`Last ${num} ${quickUnit}`, seconds);
  };

  return (
    <div style={{ position: 'relative', display: 'inline-block' }} ref={popoverRef}>
      <button 
        type="button" 
        className="btn btn--sm" 
        onClick={() => setIsOpen(!isOpen)}
        style={{ display: 'flex', alignItems: 'center', gap: '8px', backgroundColor: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)' }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
          <line x1="16" y1="2" x2="16" y2="6" />
          <line x1="8" y1="2" x2="8" y2="6" />
          <line x1="3" y1="10" x2="21" y2="10" />
        </svg>
        {currentLabel}
      </button>

      {isOpen && (
        <div style={{
          position: 'absolute',
          top: '100%',
          right: 0,
          marginTop: '4px',
          backgroundColor: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: '4px',
          boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
          width: '320px',
          zIndex: 1000,
          padding: '12px'
        }}>
          
          <div style={{ marginBottom: '16px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text)' }}>Quick select</div>
              <div style={{ display: 'flex', gap: '4px' }}>
                <button type="button" className="btn btn--sm" style={{ padding: '0 8px' }} onClick={handleStepBack} title="Step back">
                  &lt;
                </button>
                <button type="button" className="btn btn--sm" style={{ padding: '0 8px' }} onClick={handleStepForward} title="Step forward">
                  &gt;
                </button>
              </div>
            </div>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              <select className="input input--sm" disabled style={{ width: '70px' }} value="last">
                <option value="last">Last</option>
              </select>
              <input 
                type="number" 
                className="input input--sm" 
                value={quickNum} 
                onChange={e => setQuickNum(e.target.value)}
                style={{ width: '60px' }} 
              />
              <select 
                className="input input--sm" 
                value={quickUnit} 
                onChange={e => setQuickUnit(e.target.value)}
                style={{ flex: 1 }}
              >
                <option value="minutes">minutes</option>
                <option value="hours">hours</option>
                <option value="days">days</option>
              </select>
              <button type="button" className="btn btn--sm btn--primary" onClick={handleQuickApply}>Apply</button>
            </div>
          </div>

          <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text)', marginBottom: '8px' }}>Commonly used</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
            <button type="button" className="btn-link" style={linkStyle} onClick={() => handleCommonClick('Last 15 minutes', 900)}>Last 15 minutes</button>
            <button type="button" className="btn-link" style={linkStyle} onClick={() => handleCommonClick('Last 30 minutes', 1800)}>Last 30 minutes</button>
            <button type="button" className="btn-link" style={linkStyle} onClick={() => handleCommonClick('Last 1 hour', 3600)}>Last 1 hour</button>
            <button type="button" className="btn-link" style={linkStyle} onClick={() => handleCommonClick('Last 12 hours', 43200)}>Last 12 hours</button>
            <button type="button" className="btn-link" style={linkStyle} onClick={() => handleCommonClick('Last 24 hours', 86400)}>Last 24 hours</button>
            <button type="button" className="btn-link" style={linkStyle} onClick={handleToday}>Today</button>
            <button type="button" className="btn-link" style={linkStyle} onClick={handleThisWeek}>This week</button>
            <button type="button" className="btn-link" style={linkStyle} onClick={() => handleCommonClick('Last 5 days', 86400 * 5)}>Last 5 days (Logs limit)</button>
            <button type="button" className="btn-link" style={linkStyle} onClick={() => handleCommonClick('Last 7 days', 86400 * 7)}>Last 7 days (Metrics limit)</button>
          </div>
        </div>
      )}
    </div>
  );
}

const linkStyle = {
  textAlign: 'left',
  padding: '4px 8px',
  color: 'var(--ok)', // using an existing accent color from the system
  backgroundColor: 'transparent',
  border: 'none',
  cursor: 'pointer',
  fontSize: '13px'
};
