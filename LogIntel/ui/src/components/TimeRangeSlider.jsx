import React, { useState, useEffect, useRef, useCallback } from 'react';

export default function TimeRangeSlider({ initialStart, initialEnd, onChange }) {
  const [minTime] = useState(Math.floor(Date.now() / 1000) - 7 * 24 * 3600); // 7 days ago
  const [maxTime] = useState(Math.floor(Date.now() / 1000)); // Now

  const [minVal, setMinVal] = useState(initialStart || (maxTime - 24 * 3600)); // default last 24h
  const [maxVal, setMaxVal] = useState(initialEnd || maxTime);
  const range = useRef(null);

  // Convert timestamp to a percentage
  const getPercent = useCallback(
    (value) => Math.round(((value - minTime) / (maxTime - minTime)) * 100),
    [minTime, maxTime]
  );

  useEffect(() => {
    if (maxVal) {
      const minPercent = getPercent(minVal);
      const maxPercent = getPercent(maxVal);
      if (range.current) {
        range.current.style.left = `${minPercent}%`;
        range.current.style.width = `${maxPercent - minPercent}%`;
      }
    }
  }, [minVal, maxVal, getPercent]);

  // Debounce the onChange callback to avoid spamming API
  useEffect(() => {
    const handler = setTimeout(() => {
      onChange(minVal, maxVal);
    }, 500);
    return () => clearTimeout(handler);
  }, [minVal, maxVal, onChange]);

  const formatTime = (ts) => {
    return new Date(ts * 1000).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    });
  };

  return (
    <div style={{ position: 'relative', width: '100%', display: 'flex', alignItems: 'center', height: '40px' }}>
      <input
        type="range"
        min={minTime}
        max={maxTime}
        value={minVal}
        onChange={(event) => {
          const value = Math.min(Number(event.target.value), maxVal - 3600);
          setMinVal(value);
        }}
        className="thumb thumb--left"
        style={{ zIndex: minVal > maxTime - 100 && '5' }}
      />
      <input
        type="range"
        min={minTime}
        max={maxTime}
        value={maxVal}
        onChange={(event) => {
          const value = Math.max(Number(event.target.value), minVal + 3600);
          setMaxVal(value);
        }}
        className="thumb thumb--right"
      />
      
      <div className="slider">
        <div className="slider__track" />
        <div ref={range} className="slider__range" />
      </div>

      <div style={{ position: 'absolute', top: '25px', width: '100%', display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: 'var(--text-3)' }}>
        <span>{formatTime(minVal)}</span>
        <span>{formatTime(maxVal)}</span>
      </div>
    </div>
  );
}
