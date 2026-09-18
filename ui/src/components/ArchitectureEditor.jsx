import { useEffect, useRef, useState } from 'react';
import { getArchitecture, getSystems, publishArchitecture, saveArchitecture } from '../api';
import { useToast } from '../toast';
import './architecture.css';

const blank = () => ({ namespaces: [], services: [], edges: [], playbooks: [] });
const slug = (name) => name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 70);
const WORLD_WIDTH = 1000;
const CANVAS_HEIGHT = 560;
const LANE_HEIGHT = 210;
const NODE_WIDTH = 140;
const NODE_HEIGHT = 48;
const ZOOM_MIN = 0.25;
const ZOOM_MAX = 3;

function layout(architecture) {
  // Leave legacy, unassigned node positions intact when opening an older map.
  const unassigned = architecture.services.filter((node) => !node.namespace);
  const unassignedHeight = Math.max(CANVAS_HEIGHT, ...unassigned.map((node) => node.y + 65));
  const lanes = [{ name: '', top: 0, height: unassignedHeight }];
  (architecture.namespaces || []).forEach((name, index) => {
    lanes.push({ name, top: unassignedHeight + index * LANE_HEIGHT, height: LANE_HEIGHT });
  });
  return { lanes, height: unassignedHeight + (architecture.namespaces || []).length * LANE_HEIGHT };
}

function namespaceAt(lanes, y) {
  return lanes.find((lane) => y >= lane.top && y < lane.top + lane.height)?.name || '';
}

function nodeAt(services, point) {
  return services.find((node) => Math.abs(node.x - point.x) <= NODE_WIDTH / 2
    && Math.abs(node.y - point.y) <= NODE_HEIGHT / 2);
}

export default function ArchitectureEditor({ system, onClose }) {
  const toast = useToast();
  const [environments, setEnvironments] = useState([]);
  const [environment, setEnvironment] = useState('');
  const [draft, setDraft] = useState(blank);
  const [revision, setRevision] = useState(0);
  const [publishedRevision, setPublishedRevision] = useState(0);
  const [selected, setSelected] = useState('');
  const [newService, setNewService] = useState('');
  const [newNamespace, setNewNamespace] = useState('');
  const [serviceNamespace, setServiceNamespace] = useState('');
  const [tool, setTool] = useState('move');
  const [view, setView] = useState({ x: 0, y: 0, width: WORLD_WIDTH, height: CANVAS_HEIGHT });
  const [linkPreview, setLinkPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [discovered, setDiscovered] = useState([]);
  const canvas = useRef(null);
  const gesture = useRef(null);
  const architectureLayout = layout(draft);
  const zoom = WORLD_WIDTH / view.width;

  useEffect(() => {
    let active = true;
    getSystems().then(({ systems }) => {
      if (!active) return;
      const current = systems?.find((item) => item.id === system.id);
      setEnvironments(current?.environments || []);
      setDiscovered((current?.services || []).map((service) => service.name));
      setEnvironment(current?.environments?.[0] || 'default');
    }).catch((error) => toast.error('Could not discover services', { detail: error.message }));
    return () => { active = false; };
  }, [system.id]);

  useEffect(() => {
    if (!environment) return;
    let active = true;
    getArchitecture(system.id, environment).then((data) => {
      if (!active) return;
      const loaded = { ...blank(), ...data.draft };
      setDraft(loaded);
      setRevision(data.revision || 0);
      setPublishedRevision(data.published_revision || 0);
      setSelected('');
      setServiceNamespace('');
      const scale = Math.max(1, (layout(loaded).height + 30) / CANVAS_HEIGHT);
      setView({ x: 0, y: 0, width: WORLD_WIDTH * scale, height: CANVAS_HEIGHT * scale });
      setDirty(false);
    }).catch((error) => toast.error('Could not load architecture', { detail: error.message }));
    return () => { active = false; };
  }, [system.id, environment]);

  const change = (update) => { setDraft((old) => update(old)); setDirty(true); };
  const addService = (name) => {
    const clean = name.trim();
    if (!clean || draft.services.some((node) => node.name === clean)) return;
    const lane = architectureLayout.lanes.find((item) => item.name === serviceNamespace) || architectureLayout.lanes[0];
    const count = draft.services.filter((node) => (node.namespace || '') === lane.name).length;
    change((old) => ({ ...old, services: [...old.services, {
      name: clean, namespace: lane.name || null,
      x: 110 + (count % 5) * 175,
      y: lane.top + 85 + Math.floor(count / 5) * 70,
    }] }));
    setSelected(clean);
    setNewService('');
  };
  const removeService = () => {
    change((old) => ({
      ...old,
      services: old.services.filter((node) => node.name !== selected),
      edges: old.edges.filter((edge) => edge.source !== selected && edge.target !== selected),
      playbooks: old.playbooks.filter((book) => book.service !== selected),
    }));
    setSelected('');
  };
  const addEdge = (source, target) => {
    if (!source || !target || source === target) return;
    change((old) => old.edges.some((edge) => edge.source === source && edge.target === target)
      ? old : { ...old, edges: [...old.edges, { source, target, kind: 'calls' }] });
  };
  const addNamespace = () => {
    const name = newNamespace.trim().toLowerCase();
    if (!/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(name)) {
      toast.error('Use a Kubernetes namespace name: lowercase letters, digits, and hyphens');
      return;
    }
    if (draft.namespaces.includes(name)) return;
    change((old) => ({ ...old, namespaces: [...old.namespaces, name] }));
    setNewNamespace('');
    setServiceNamespace(name);
    fitCanvas(architectureLayout.height + LANE_HEIGHT);
  };
  const assignNamespace = (name) => {
    const lane = architectureLayout.lanes.find((item) => item.name === name);
    if (!lane || !selected) return;
    change((old) => ({ ...old, services: old.services.map((node) => node.name === selected
      ? { ...node, namespace: name || null, y: Math.round(lane.top + lane.height / 2) } : node) }));
  };
  const removeNamespace = (name) => {
    change((old) => {
      let count = old.services.filter((node) => !node.namespace).length;
      const removedIndex = old.namespaces.indexOf(name);
      return { ...old, namespaces: old.namespaces.filter((item) => item !== name),
        services: old.services.map((node) => {
          if (node.namespace !== name) {
            const index = old.namespaces.indexOf(node.namespace);
            return index > removedIndex ? { ...node, y: Math.max(35, node.y - LANE_HEIGHT) } : node;
          }
          const position = count++;
          return { ...node, namespace: null, x: 110 + (position % 5) * 175,
            y: 100 + Math.floor(position / 5) * 80 };
        }) };
    });
    if (serviceNamespace === name) setServiceNamespace('');
  };
  const addPlaybook = () => {
    const id = `${slug(selected)}-${Date.now().toString(36)}`;
    change((old) => ({ ...old, playbooks: [...old.playbooks, {
      id, name: `${selected} investigation`, service: selected,
      signal_types: [], checks: [], metric_queries: [],
    }] }));
  };
  const editPlaybook = (id, patch) => change((old) => ({ ...old,
    playbooks: old.playbooks.map((book) => book.id === id ? { ...book, ...patch } : book),
  }));
  const save = async () => {
    setBusy(true);
    try {
      const result = await saveArchitecture(system.id, environment, draft);
      setRevision(result.revision);
      setDirty(false);
      toast.success('Draft saved');
    } catch (error) { toast.error('Could not save draft', { detail: error.message }); }
    finally { setBusy(false); }
  };
  const publish = async () => {
    setBusy(true);
    try {
      const saved = dirty ? await saveArchitecture(system.id, environment, draft) : null;
      const result = await publishArchitecture(system.id, environment);
      setRevision(saved?.revision || result.revision);
      setPublishedRevision(result.published_revision);
      setDirty(false);
      toast.success('Architecture and playbooks published');
    } catch (error) { toast.error('Could not publish', { detail: error.message }); }
    finally { setBusy(false); }
  };
  const worldPoint = (event) => {
    const svg = canvas.current;
    if (!svg?.getScreenCTM()) return null;
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    return point.matrixTransform(svg.getScreenCTM().inverse());
  };
  const fitCanvas = (height = architectureLayout.height) => {
    const scale = Math.max(1, (height + 30) / CANVAS_HEIGHT);
    setView({ x: 0, y: 0, width: WORLD_WIDTH * scale, height: CANVAS_HEIGHT * scale });
  };
  const zoomAt = (factor, point) => {
    setView((old) => {
      const nextZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, (WORLD_WIDTH / old.width) * factor));
      const width = WORLD_WIDTH / nextZoom;
      const height = CANVAS_HEIGHT / nextZoom;
      const anchor = point || { x: old.x + old.width / 2, y: old.y + old.height / 2 };
      return { x: anchor.x - (anchor.x - old.x) * width / old.width,
        y: anchor.y - (anchor.y - old.y) * height / old.height, width, height };
    });
  };
  const pointerMove = (event) => {
    const current = gesture.current;
    if (!current) return;
    const point = worldPoint(event);
    if (!point) return;
    if (current.type === 'link') {
      setLinkPreview({ source: current.source, x: point.x, y: point.y });
    } else if (current.type === 'pan') {
      const dx = (event.clientX - current.clientX) * view.width / canvas.current.getBoundingClientRect().width;
      const dy = (event.clientY - current.clientY) * view.height / canvas.current.getBoundingClientRect().height;
      setView((old) => ({ ...old, x: current.x - dx, y: current.y - dy }));
    } else if (current.type === 'move') {
      const x = Math.max(75, Math.min(925, Math.round(point.x)));
      const y = Math.max(35, Math.min(architectureLayout.height - 35, Math.round(point.y)));
      const namespace = namespaceAt(architectureLayout.lanes, y);
      change((old) => ({ ...old, services: old.services.map((node) => node.name === current.name
        ? { ...node, x, y, namespace: namespace || null } : node) }));
    }
  };
  const pointerUp = (event) => {
    const current = gesture.current;
    if (current?.type === 'link') {
      const point = worldPoint(event);
      const target = point && nodeAt(draft.services, point);
      if (target) addEdge(current.source, target.name);
    }
    gesture.current = null;
    setLinkPreview(null);
  };
  const selectedBooks = draft.playbooks.filter((book) => book.service === selected);

  return (
    <div className="card architecture-editor">
      <header className="row"><h3>Service architecture · {system.name}</h3><span className="spacer" />
        <button className="btn btn--sm btn--ghost" onClick={onClose}>Close</button></header>
      <div className="card-body">
        <div className="row architecture-toolbar">
          <label>Environment <select className="input" value={environment} onChange={(e) => setEnvironment(e.target.value)}>
            {(environments.length ? environments : ['default']).map((env) => <option key={env}>{env}</option>)}
          </select></label>
          <span className="chip">Draft {revision}{dirty ? ' · unsaved' : ''}</span>
          <span className="chip">Published {publishedRevision}</span>
          <span className="spacer" />
          <button className="btn" disabled={busy || !dirty} onClick={save}>Save draft</button>
          <button className="btn btn--primary" disabled={busy} onClick={publish}>Publish</button>
        </div>
        <p className="dim">Use Move to place services, Pan to navigate, or Connect to drag an arrow from caller to dependency. Drag services between namespace lanes or assign them in the sidebar. The agent treats this map and its playbooks as guidance; observed telemetry remains the evidence.</p>
        <div className="row architecture-canvas-toolbar" role="toolbar" aria-label="Architecture canvas controls">
          {['move', 'pan', 'connect'].map((item) => <button key={item} type="button"
            className={`btn btn--sm ${tool === item ? 'btn--primary' : ''}`}
            aria-pressed={tool === item} onClick={() => { setTool(item); gesture.current = null; setLinkPreview(null); }}>
            {item === 'connect' ? '→ Connect' : item === 'pan' ? '✥ Pan' : 'Move'}
          </button>)}
          <span className="spacer" />
          <button type="button" className="btn btn--sm" aria-label="Zoom out" onClick={() => zoomAt(1 / 1.25)}>−</button>
          <span className="chip" aria-live="polite">{Math.round(zoom * 100)}%</span>
          <button type="button" className="btn btn--sm" aria-label="Zoom in" onClick={() => zoomAt(1.25)}>+</button>
          <button type="button" className="btn btn--sm" onClick={() => fitCanvas()}>Fit</button>
        </div>
        <div className="architecture-layout">
          <svg ref={canvas} className={`architecture-canvas architecture-canvas--${tool}`}
            viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`}
            role="img" aria-label="Editable service architecture diagram"
            onWheel={(event) => { event.preventDefault(); const point = worldPoint(event); zoomAt(event.deltaY < 0 ? 1.15 : 1 / 1.15, point); }}
            onPointerDown={(event) => {
              if (tool !== 'pan') return;
              gesture.current = { type: 'pan', clientX: event.clientX, clientY: event.clientY, x: view.x, y: view.y };
              event.currentTarget.setPointerCapture(event.pointerId);
            }}
            onPointerMove={pointerMove} onPointerUp={pointerUp}
            onPointerCancel={() => { gesture.current = null; setLinkPreview(null); }}>
            <defs><marker id="architecture-arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker></defs>
            {architectureLayout.lanes.map((lane, index) => <g key={lane.name || 'unassigned'}>
              <rect className={`architecture-lane ${index % 2 ? 'architecture-lane--alternate' : ''}`}
                x="0" y={lane.top} width={WORLD_WIDTH} height={lane.height} />
              <text className="architecture-lane-label" x="22" y={lane.top + 28}>
                {lane.name ? `Namespace: ${lane.name}` : 'Unassigned services'}
              </text>
            </g>)}
            {draft.edges.map((edge) => {
              const from = draft.services.find((node) => node.name === edge.source);
              const to = draft.services.find((node) => node.name === edge.target);
              if (!from || !to) return null;
              return <g key={`${edge.source}:${edge.target}`}>
                <line x1={from.x} y1={from.y} x2={to.x} y2={to.y} className="architecture-edge" markerEnd="url(#architecture-arrow)" />
                <text x={(from.x + to.x) / 2} y={(from.y + to.y) / 2 - 6} textAnchor="middle" className="architecture-edge-label">{edge.kind}</text>
              </g>;
            })}
            {linkPreview && (() => {
              const source = draft.services.find((node) => node.name === linkPreview.source);
              return source && <line className="architecture-edge architecture-edge--preview"
                x1={source.x} y1={source.y} x2={linkPreview.x} y2={linkPreview.y}
                markerEnd="url(#architecture-arrow)" />;
            })()}
            {draft.services.map((node) => <g key={node.name} className={`architecture-node${selected === node.name ? ' is-selected' : ''}`}
              transform={`translate(${node.x},${node.y})`} onPointerDown={(event) => {
                event.stopPropagation();
                setSelected(node.name);
                if (tool === 'pan') return;
                gesture.current = tool === 'connect' ? { type: 'link', source: node.name } : { type: 'move', name: node.name };
                if (tool === 'connect') setLinkPreview({ source: node.name, x: node.x, y: node.y });
                event.currentTarget.setPointerCapture(event.pointerId);
              }}>
              <rect x="-70" y="-24" width="140" height="48" rx="9" />
              <text textAnchor="middle" dominantBaseline="middle">{node.name.length > 19 ? `${node.name.slice(0, 17)}…` : node.name}</text>
            </g>)}
          </svg>
          <aside className="architecture-side">
            <h4>Namespaces</h4>
            <div className="row"><input className="input" value={newNamespace} placeholder="namespace name"
              aria-label="New namespace name" onChange={(e) => setNewNamespace(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') addNamespace(); }} />
              <button className="btn btn--sm" onClick={addNamespace}>Add</button></div>
            {(draft.namespaces || []).map((name) => <div className="row architecture-namespace" key={name}>
              <span>{name}</span><span className="spacer" />
              <button className="btn btn--sm btn--ghost" aria-label={`Remove namespace ${name}`}
                onClick={() => removeNamespace(name)}>Remove</button>
            </div>)}
            <h4>Add service</h4>
            <label>Namespace for new service<select className="input" value={serviceNamespace}
              onChange={(e) => setServiceNamespace(e.target.value)}>
              <option value="">Unassigned</option>
              {(draft.namespaces || []).map((name) => <option key={name} value={name}>{name}</option>)}
            </select></label>
            <div className="row"><input className="input" value={newService} placeholder="service name" onChange={(e) => setNewService(e.target.value)} />
              <button className="btn btn--sm" onClick={() => addService(newService)}>Add</button></div>
            <select className="input" value="" onChange={(e) => addService(e.target.value)}>
              <option value="">Add discovered service…</option>
              {discovered.filter((name) => !draft.services.some((node) => node.name === name)).map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
            {selected && <>
              <h4>{selected}</h4>
              <label>Namespace<select className="input"
                value={draft.services.find((node) => node.name === selected)?.namespace || ''}
                onChange={(e) => assignNamespace(e.target.value)}>
                <option value="">Unassigned</option>
                {(draft.namespaces || []).map((name) => <option key={name} value={name}>{name}</option>)}
              </select></label>
              <span className="dim">Choose Connect, then drag from this service to its dependency.</span>
              {draft.edges.filter((edge) => edge.source === selected || edge.target === selected).map((edge) =>
                <div className="row architecture-link" key={`${edge.source}:${edge.target}`}>
                  <span>{edge.source} → {edge.target}</span><span className="spacer" />
                  <button className="btn btn--sm btn--ghost" onClick={() => change((old) => ({ ...old, edges: old.edges.filter((item) => item !== edge) }))}>Remove</button>
                </div>)}
              <button className="btn btn--sm btn--ghost btn--danger" onClick={removeService}>Remove service</button>
              <h4>Playbooks</h4><button className="btn btn--sm" onClick={addPlaybook}>Add playbook</button>
              {selectedBooks.map((book) => <div className="architecture-book" key={book.id}>
                <label>Name<input className="input" value={book.name} onChange={(e) => editPlaybook(book.id, { name: e.target.value })} /></label>
                <label>Signal types (comma separated)<input className="input" value={book.signal_types.join(', ')}
                  onChange={(e) => editPlaybook(book.id, { signal_types: e.target.value.split(',').map((item) => item.trim()).filter(Boolean) })} /></label>
                <label>Investigation checks (one per line)<textarea className="input" rows="5" value={book.checks.join('\n')}
                  onChange={(e) => editPlaybook(book.id, { checks: e.target.value.split('\n').filter(Boolean) })} /></label>
                <label>Prometheus metric hints (one metric name per line)<textarea className="input" rows="3" value={book.metric_queries.join('\n')}
                  onChange={(e) => editPlaybook(book.id, { metric_queries: e.target.value.split('\n').map((item) => item.trim()).filter(Boolean) })} /></label>
                <button className="btn btn--sm btn--ghost" onClick={() => change((old) => ({ ...old, playbooks: old.playbooks.filter((item) => item.id !== book.id) }))}>Remove playbook</button>
              </div>)}
            </>}
          </aside>
        </div>
      </div>
    </div>
  );
}
