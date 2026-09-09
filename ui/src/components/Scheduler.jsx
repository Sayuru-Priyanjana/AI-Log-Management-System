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

  // Centralized completion handler for automated agent runs (scheduled scans & auto-investigated alerts)
  useEffect(() => {
    if (status === 'complete' && result && meta?.auto && notifiedRef.current !== result.id) {
      notifiedRef.current = result.id;
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
        const currentHHMM = now.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' });
        const todayDate = now.toLocaleDateString();

        for (const system of systems) {
          const { values } = await getSystemIntegrations(system.id).catch(() => ({ values: null }));
          if (!values) continue;

          if (values.auto_scan_enabled && values.scan_time) {
            const [scanHour, scanMinute] = values.scan_time.split(':').map(Number);
            const scanTimeToday = new Date();
            scanTimeToday.setHours(scanHour, scanMinute, 0, 0);

            if (now >= scanTimeToday) {
              const scanKey = `${system.id}-${todayDate}`;
              
              // Persist scan history in localStorage so page reloads or multiple tabs 
              // don't trigger duplicate scans.
              const storedHistory = JSON.parse(localStorage.getItem('logintel_scheduled_scans') || '{}');
              if (storedHistory[scanKey]) continue;

              storedHistory[scanKey] = true;
              localStorage.setItem('logintel_scheduled_scans', JSON.stringify(storedHistory));

              const targetService = system.services?.[0]?.name || 'cluster-wide';
              const nowISO = new Date().toISOString();
              const oneHourAgoISO = new Date(Date.now() - 3600000).toISOString();

              const navState = {
                system_id: system.id,
                environment: system.environments?.[0],
                service: targetService,
                service_hint: targetService,
                question: `Perform a routine daily health scan on ${targetService}. Look for any anomalies in metrics or logs over the past hour.`,
                start_time: oneHourAgoISO,
                end_time: nowISO,
              };

              const scanMeta = {
                kind: 'scheduled',
                label: `Scheduled Scan (${currentHHMM})`,
                serviceLabel: targetService,
                auto: true,
                systemId: system.id,
                systemName: system.name,
              };

              toast.info(`Starting scheduled AI scan for ${system.name}`);
              startInvestigation(navState, scanMeta);
              break; // Start one at a time
            }
          }
        }
      } catch (err) {
        console.error("Scheduler error:", err);
      }
    }, 30000); // check every 30 seconds

    return () => clearInterval(tick);
  }, [status, startInvestigation, toast]);

  return null;
}

