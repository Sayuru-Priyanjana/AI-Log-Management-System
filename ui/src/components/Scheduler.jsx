import { useEffect, useRef } from 'react';
import { getSystemIntegrations, getSystems, notifyIntegrations } from '../api';
import { useInvestigation } from '../InvestigationContext';
import { useToast } from '../toast';
import { usePreferences } from '../preferences';
import { investigationCard } from '../teams';
import { setAlertStatus } from '../mockData';

export default function Scheduler() {
  const { startInvestigation, status, result, meta } = useInvestigation();
  const toast = useToast();
  const { formatStamp } = usePreferences();
  const notifiedRef = useRef(null);
  const inFlightScanRef = useRef(new Set());

  // Centralized completion handler for automated agent runs (scheduled scans & auto-investigated alerts)
  useEffect(() => {
    if (status === 'complete' && result && meta?.auto && notifiedRef.current !== result.id) {
      notifiedRef.current = result.id;
      
      const notifiedKey = `notified-investigation-${result.id}`;
      if (localStorage.getItem(notifiedKey)) return;
      localStorage.setItem(notifiedKey, 'true');

      const targetSystemId = meta.systemId || result.plan?.system_id || result.system_id;
      if (!targetSystemId) return;

      // If an alert was being auto-investigated, mark it handled and notify UI components
      if (meta?.kind === 'alert' && meta?.alertId) {
        setAlertStatus(meta.alertId, 'handled');
        window.dispatchEvent(new CustomEvent('logintel:alertStatusChanged', {
          detail: { id: meta.alertId, status: 'handled' }
        }));
      }

      // Check system configuration: Notify integrations with agent response
      getSystemIntegrations(targetSystemId)
        .then(async ({ values }) => {
          if (values?.notify_on_scan_result_enabled) {
            try {
              const card = investigationCard({
                result,
                systemName: meta.systemName || targetSystemId,
                systemId: targetSystemId,
                label: meta.label,
                formatStamp,
              });
              const response = await notifyIntegrations(targetSystemId, card);
              if (response && response.ok === false) {
                console.warn('Teams notification error:', response.detail);
              } else {
                toast.success(`Sent ${meta.kind === 'scheduled' ? 'scheduled scan' : 'investigation'} result to Teams`);
              }
            } catch (err) {
              console.error('Failed to notify integrations with agent response:', err);
              toast.error('Could not send agent response to integrations', { detail: err.message });
            }
          }
        })
        .catch(console.error);
    }
  }, [status, result, meta, toast, formatStamp]);

  // Scheduled daily AI agent scan checker
  useEffect(() => {
    const tick = setInterval(async () => {
      // Do not interrupt if an investigation is currently in-flight
      if (status === 'connecting' || status === 'streaming') return;

      try {
        const { systems } = await getSystems();
        if (!systems || systems.length === 0) return;

        const now = new Date();
        const isoDate = now.toISOString().slice(0, 10); // Standardized YYYY-MM-DD
        const currentHHMM = now.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' });

        for (const system of systems) {
          const { values } = await getSystemIntegrations(system.id).catch(() => ({ values: null }));
          if (!values) continue;

          if (values.auto_scan_enabled && values.scan_time) {
            const [scanHour, scanMinute] = values.scan_time.split(':').map(Number);
            const scanTimeToday = new Date();
            scanTimeToday.setHours(scanHour, scanMinute, 0, 0);

            if (now >= scanTimeToday) {
              // Add a random jitter (0-2s) to prevent cross-tab race conditions
              await new Promise(r => setTimeout(r, Math.random() * 2000));

              // Keyed by system, date, and configured scan_time to prevent duplicate runs across tabs
              // while still allowing re-testing if the user adjusts the scan_time.
              const scanKey = `${system.id}-${isoDate}-${values.scan_time}`;
              
              const storedHistory = JSON.parse(localStorage.getItem('logintel_scheduled_scans') || '{}');
              if (storedHistory[scanKey] || inFlightScanRef.current.has(scanKey)) continue;

              // Immediately acquire lock before starting
              inFlightScanRef.current.add(scanKey);
              storedHistory[scanKey] = Date.now();
              localStorage.setItem('logintel_scheduled_scans', JSON.stringify(storedHistory));

              const nowISO = new Date().toISOString();
              const oneHourAgoISO = new Date(Date.now() - 3600000).toISOString();

              // Scans the overall system across all services (no hardcoded single service)
              const navState = {
                system_id: system.id,
                environment: system.environments?.[0],
                question: `Perform a routine daily health scan on the entire ${system.name} system. Look for any anomalies across all services, metrics, and logs over the past hour.`,
                start_time: oneHourAgoISO,
                end_time: nowISO,
              };

              const scanMeta = {
                kind: 'scheduled',
                label: `Daily Health Scan (${currentHHMM})`,
                serviceLabel: 'Entire System',
                auto: true,
                systemId: system.id,
                systemName: system.name,
              };

              toast.info(`Starting scheduled daily health scan for ${system.name} (Entire System)`);
              startInvestigation(navState, scanMeta);
              break; // Start one at a time
            }
          }
        }
      } catch (err) {
        console.error("Scheduler error:", err);
      }
    }, 15000); // Check every 15 seconds

    return () => clearInterval(tick);
  }, [status, startInvestigation, toast]);

  return null;
}

