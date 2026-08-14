import { useEffect, useRef } from 'react';
import { getSystemIntegrations, getSystems } from '../api';
import { useInvestigation } from '../InvestigationContext';
import { useToast } from '../toast';

export default function Scheduler() {
  const { startInvestigation, status } = useInvestigation();
  const toast = useToast();

  useEffect(() => {
    const tick = setInterval(async () => {
      // Don't start a new scheduled scan if one is already running
      if (status !== 'idle') return;

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

              const targetService = system.services?.[0]?.name || 'unknown';
              
              const navState = {
                system_id: system.id,
                environment: system.environments?.[0],
                service: targetService,
                question: `Perform a routine daily health scan on ${targetService}. Look for any anomalies in metrics or logs.`,
                kind: 'scheduled',
                label: `Scheduled Scan (${currentHHMM})`,
                serviceLabel: targetService,
              };

              const meta = { kind: 'scheduled', label: `Scheduled Scan (${currentHHMM})`, serviceLabel: targetService };
              
              toast.info(`Starting scheduled AI scan for ${system.name}`);
              startInvestigation(navState, meta);
              
              // We can't await the investigation here easily because startInvestigation is async but status updates asynchronously
              // The notification for scan results will be handled in AgentPage.jsx when status changes to 'complete'.
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
