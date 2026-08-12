import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { getHealth, getSystems } from '../api';
import { useToast } from '../toast';
import InvestigationForm from './InvestigationForm';
import InvestigationResults from './InvestigationResults';

/**
 * Systems on the left, the selected one on the right, both full height.
 *
 * The right pane shows the system as a header strip and a services table — a
 * dense, sortable-shaped list of what is actually shipping logs. It replaced a
 * drawn "cluster architecture" tree of boxes and connector lines: that took
 * roughly a screen of height to say a cluster contains environments and
 * services, which the two rows beneath it already said, and it could not show
 * the log counts that make the list worth reading.
 */
export default function WorkstationPage() {
  const location = useLocation();
  const toast = useToast();
  const [prefill] = useState(location.state?.prefill || null);

  const [systems, setSystems] = useState([]);
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState(prefill?.system_id || null);
  const [request, setRequest] = useState(null);

  useEffect(() => {
    let mounted = true;
    Promise.all([getSystems(), getHealth()])
      .then(([systemData, healthData]) => {
        if (!mounted) return;
        const list = systemData.systems || [];
        setSystems(list);
        setHealth(healthData);
        setSelectedId((current) => current || list[0]?.id || null);
      })
      .catch((err) => mounted && toast.error('Could not load systems', { detail: err.message }))
      .finally(() => mounted && setLoading(false));
    return () => { mounted = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selected = systems.find((system) => system.id === selectedId);

  return (
    <div className="ws">
      <aside className="ws-side">
        <div className="ws-side-head">
          <h4>Systems</h4>
          <span className="spacer" />
          <span className="dim">{systems.length}</span>
        </div>
        <div className="ws-side-list">
          {loading && <div className="empty">Loading…</div>}
          {!loading && systems.length === 0 && (
            <div className="empty">No systems assigned to you.</div>
          )}
          {systems.map((system) => (
            <button key={system.id} type="button"
              className={`ws-item ${system.id === selectedId ? 'is-active' : ''}`}
              onClick={() => { setSelectedId(system.id); setRequest(null); }}>
              <span className="ws-item-name">{system.name}</span>
              <span className="ws-item-meta">
                {system.id} · {system.services?.length || 0} svc
              </span>
            </button>
          ))}
        </div>
      </aside>

      <section className="ws-main">
        {!selected ? (
          <div className="empty" style={{ margin: 'auto' }}>
            {loading ? 'Loading…' : 'Select a system.'}
          </div>
        ) : (
          <>
            <div className="ws-bar">
              <h2>{selected.name}</h2>
              <span className="chip chip--mono">{selected.id}</span>
              {(selected.environments || []).map((env) => (
                <span key={env} className="chip">{env}</span>
              ))}
              <span className="spacer" />
              {health && (
                <span className="row" style={{ gap: 5 }}>
                  <span className={`dot dot--${tone(health.status)}`} />
                  <span className="dim">integrations {health.status}</span>
                </span>
              )}
              {request && (
                <button type="button" className="btn btn--sm" onClick={() => setRequest(null)}>
                  New query
                </button>
              )}
            </div>

            <div className="ws-body">
              {request ? (
                <InvestigationResults request={request}
                  onFollowUp={(question) => setRequest({ ...request, question, _at: Date.now() })} />
              ) : (
                <div className="grid grid--2" style={{ alignItems: 'start' }}>
                  <div className="card">
                    <header><h3>Ask</h3></header>
                    <div className="card-body">
                      <InvestigationForm onSubmit={setRequest} initial={prefill}
                        lockedSystem={selected} />
                    </div>
                  </div>

                  <div className="card">
                    <header>
                      <h3>Services</h3>
                      <span className="spacer" />
                      <span className="dim">{selected.services?.length || 0} shipping logs</span>
                    </header>
                    {selected.services?.length ? (
                      <table className="table">
                        <thead>
                          <tr><th>Service</th><th style={{ width: 110, textAlign: 'right' }}>Documents</th></tr>
                        </thead>
                        <tbody>
                          {[...selected.services]
                            .sort((a, b) => (b.log_count || 0) - (a.log_count || 0))
                            .map((service) => (
                              <tr key={service.name}>
                                <td className="mono">{service.name}</td>
                                <td className="num">{(service.log_count || 0).toLocaleString()}</td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    ) : (
                      <div className="empty">
                        Nothing has shipped logs for this system yet.
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}

function tone(status) {
  if (status === 'ok') return 'ok';
  if (status === 'unreachable') return 'err';
  return 'warn';
}
