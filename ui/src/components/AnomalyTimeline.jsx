import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { usePreferences } from '../preferences';
import { useToast } from '../toast';
import { setAlertStatus } from '../mockData';
import { detectionNavState } from '../investigate';

/**
 * AnomalyTimeline renders a surveillance-style scrub bar highlighting
 * system alerts and anomalies.
 *
 * Hovering a blip still shows the plain info tooltip it always has. Clicking
 * a red or yellow one — a real detection, not the informational blue ones —
 * pins a second, interactive popover with an "Analyse with AI" button, which
 * hands the agent that detection's own payload and a window bracketing when
 * it fired, then starts the investigation immediately. It is the same flow
 * "Investigate with agent" in the alerts panel already offers; this is a
 * second, faster way to reach it from directly on the timeline.
 */
export default function AnomalyTimeline({ alerts, start, end, system }) {
  const navigate = useNavigate();
  const toast = useToast();
  const { formatStamp } = usePreferences();

  const [hoveredAlert, setHoveredAlert] = useState(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });

  // The pinned popover: set by a click, not a hover, and stays open until the
  // reader dismisses it, clicks elsewhere, or navigates away with it.
  const [pinnedAlert, setPinnedAlert] = useState(null);
  const [pinnedPos, setPinnedPos] = useState({ x: 0, y: 0 });
  const popoverRef = useRef(null);

  const windowMs = end * 1000 - start * 1000;

  const handleMouseEnter = (e, alert) => {
    const rect = e.currentTarget.getBoundingClientRect();
    setTooltipPos({
      x: rect.left + rect.width / 2,
      y: rect.top - 8 // Position above the blip
    });
    setHoveredAlert(alert);
  };

  const handleBlipClick = (e, alert) => {
    e.stopPropagation();
    const rect = e.currentTarget.getBoundingClientRect();
    setPinnedPos({ x: rect.left + rect.width / 2, y: rect.top - 8 });
    setPinnedAlert(alert);
  };

  // Dismissed by clicking anywhere outside the popover, or by Escape — the
  // same convention ChartModal's service dropdown uses. A 'mousedown'
  // listener rather than 'click' so that clicking a *different* blip closes
  // this popover before that blip's own click handler opens the next one,
  // instead of the two racing.
  useEffect(() => {
    if (!pinnedAlert) return;
    const handlePointerDown = (e) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target)) {
        setPinnedAlert(null);
      }
    };
    const handleKey = (e) => { if (e.key === 'Escape') setPinnedAlert(null); };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKey);
    };
  }, [pinnedAlert]);

  const analyse = (alert) => {
    if (!system?.id) {
      toast.error('No system selected');
      return;
    }
    const navState = detectionNavState(alert, system, formatStamp);
    // Mirrors AlertsPanel.investigate(): triage state is the app's own, not
    // OpenSearch's, so it is set here rather than left for the alerts panel
    // to notice on its own next refresh.
    setAlertStatus(alert.id, 'investigating');
    setPinnedAlert(null);
    navigate('/agent', { state: navState });
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

        // "Analyse with AI" is offered on genuine detections (red/yellow)
        // only. The informational blue blips have no severity behind them
        // worth an auto-started investigation over.
        const clickable = severityClass === 'high' || severityClass === 'medium';

        return (
          <div
            key={alert.id || alertTimeMs}
            className={`anomaly-blip anomaly-blip--${severityClass}`}
            style={{ left: `${posPercent}%`, cursor: clickable ? 'pointer' : 'default' }}
            onMouseEnter={(e) => handleMouseEnter(e, alert)}
            onMouseLeave={() => setHoveredAlert(null)}
            onClick={clickable ? (e) => handleBlipClick(e, alert) : undefined}
          >
          </div>
        );
      })}

      {hoveredAlert && hoveredAlert !== pinnedAlert && createPortal(
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

      {pinnedAlert && createPortal(
        <div
          ref={popoverRef}
          className="timeline-popover"
          style={{
            position: 'fixed',
            left: `${pinnedPos.x}px`,
            top: `${pinnedPos.y}px`,
            transform: 'translate(-50%, -100%)',
          }}
        >
          <strong>{pinnedAlert.monitor_name || pinnedAlert.title || 'Anomaly Detected'}</strong>
          <div className="time">{new Date(pinnedAlert.start_time || pinnedAlert.timestamp).toLocaleString()}</div>
          {pinnedAlert.error_message && (
            <div style={{
              fontSize: '11px', marginTop: '4px', color: 'var(--text-2)',
              whiteSpace: 'normal', wordWrap: 'break-word', lineHeight: '1.4',
            }}>
              {pinnedAlert.error_message}
            </div>
          )}
          <div className="timeline-popover-actions">
            <button
              type="button"
              className="btn btn--sm btn--primary"
              onClick={() => analyse(pinnedAlert)}
              disabled={!system?.id}
              title={system?.id ? undefined : 'No system selected'}
            >
              Analyse with AI →
            </button>
            <button type="button" className="btn btn--sm" onClick={() => setPinnedAlert(null)}>
              ✕
            </button>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
