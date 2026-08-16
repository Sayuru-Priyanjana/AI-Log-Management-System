import React from 'react';

/**
 * AnomalyTimeline renders a surveillance-style scrub bar highlighting
 * system alerts and anomalies.
 */
export default function AnomalyTimeline({ alerts, start, end }) {
  const windowMs = end * 1000 - start * 1000;

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
          >
            <div className="timeline-tooltip">
              <strong>{alert.monitor_name || alert.title || "Anomaly Detected"}</strong>
              <span className="time">{new Date(alertTimeMs).toLocaleTimeString()}</span>
              {alert.error_message && (
                <span className="dim" style={{ fontSize: '10.5px', marginTop: '2px' }}>
                  {alert.error_message}
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
