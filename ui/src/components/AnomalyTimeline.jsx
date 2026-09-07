import React, { useState } from 'react';
import { createPortal } from 'react-dom';

/**
 * AnomalyTimeline renders a surveillance-style scrub bar highlighting
 * system alerts and anomalies.
 */
export default function AnomalyTimeline({ alerts, start, end }) {
  const [hoveredAlert, setHoveredAlert] = useState(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });

  const windowMs = end * 1000 - start * 1000;

  const handleMouseEnter = (e, alert) => {
    const rect = e.currentTarget.getBoundingClientRect();
    setTooltipPos({ 
      x: rect.left + rect.width / 2, 
      y: rect.top - 8 // Position above the blip
    });
    setHoveredAlert(alert);
  };

  return (
    <div className="anomaly-timeline" title="Anomaly & Detection Timeline">
      <div className="anomaly-timeline-track"></div>
      
      {alerts && alerts.map((alert) => {
        // Calculate the percentage position on the timeline
        const alertTimeMs = alert.start_time || alert.timestamp;
        if (!alertTimeMs || alertTimeMs < start * 1000 || alertTimeMs > end * 1000) return null;
        
        const posPercent = ((alertTimeMs - start * 1000) / windowMs) * 100;
        
        let severityClass = 'info';
        if (alert.severity === "1" || alert.severity === "high") severityClass = 'high';
        else if (alert.severity === "2" || alert.severity === "medium") severityClass = 'medium';

        return (
          <div 
            key={alert.id || alertTimeMs}
            className={`anomaly-blip anomaly-blip--${severityClass}`}
            style={{ left: `${posPercent}%` }}
            onMouseEnter={(e) => handleMouseEnter(e, alert)}
            onMouseLeave={() => setHoveredAlert(null)}
          >
          </div>
        );
      })}

      {hoveredAlert && createPortal(
        <div 
          className="timeline-tooltip"
          style={{
            position: 'fixed',
            left: `${tooltipPos.x}px`,
            top: `${tooltipPos.y}px`,
            bottom: 'auto',
            transform: 'translate(-50%, -100%)',
            margin: 0,
            zIndex: 99999,
            opacity: 1,
            visibility: 'visible',
            pointerEvents: 'none'
          }}
        >
          <strong>{hoveredAlert.monitor_name || hoveredAlert.title || "Anomaly Detected"}</strong>
          <span className="time">{new Date(hoveredAlert.start_time || hoveredAlert.timestamp).toLocaleTimeString()}</span>
          {hoveredAlert.error_message && (
            <div style={{ 
              fontSize: '11px', 
              marginTop: '4px', 
              color: 'var(--text-2)', 
              whiteSpace: 'normal', 
              maxWidth: '350px', 
              wordWrap: 'break-word',
              lineHeight: '1.4'
            }}>
              {hoveredAlert.error_message}
            </div>
          )}
        </div>,
        document.body
      )}
    </div>
  );
}
