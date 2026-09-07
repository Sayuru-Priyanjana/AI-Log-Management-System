import { useRef, useState } from 'react';

/**
 * A column whose width the reader sets by dragging, and which stays set.
 *
 * The three panes were fixed at 220px / 1fr / 300px, which is a guess about
 * how someone works: a long question does not fit the entry pane, and a chat
 * list of thirty investigations needs more than 220px to show its titles. The
 * width is per-workstation, so it is stored beside the other UI preferences
 * rather than on the server.
 *
 * Pointer events rather than mouse events, so a trackpad, a pen and a touch
 * screen all work, and `setPointerCapture` keeps the drag alive when the
 * cursor outruns the 6px handle — which it always does.
 */
export function useResizableWidth(storageKey, initial, [min, max]) {
  const [width, setWidth] = useState(() => {
    const stored = Number(localStorage.getItem(storageKey));
    return Number.isFinite(stored) && stored >= min && stored <= max ? stored : initial;
  });
  const drag = useRef(null);

  const clamp = (value) => Math.min(max, Math.max(min, value));

  const onPointerDown = (event, edge) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { startX: event.clientX, startWidth: width, edge };
  };

  const onPointerMove = (event) => {
    if (!drag.current) return;
    const delta = event.clientX - drag.current.startX;
    // The right-hand pane grows as the pointer moves left, so its edge is
    // inverted. Without this the entry pane shrinks when you drag it wider.
    setWidth(clamp(drag.current.startWidth + (drag.current.edge === 'right' ? -delta : delta)));
  };

  const end = (event) => {
    if (!drag.current) return;
    drag.current = null;
    try { event.currentTarget.releasePointerCapture(event.pointerId); } catch { /* already gone */ }
    setWidth((w) => { localStorage.setItem(storageKey, String(w)); return w; });
  };

  // Keyboard: a separator that can only be dragged is unusable without a
  // pointer, and this one has a perfectly good discrete equivalent.
  const onKeyDown = (event, edge) => {
    const step = event.shiftKey ? 40 : 10;
    const dir = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0;
    if (!dir) return;
    event.preventDefault();
    setWidth((w) => {
      const next = clamp(w + dir * step * (edge === 'right' ? -1 : 1));
      localStorage.setItem(storageKey, String(next));
      return next;
    });
  };

  return { width, min, max, onPointerDown, onPointerMove, end, onKeyDown };
}

export function Resizer({ handle, edge, label }) {
  return (
    <div className="agent-resizer" role="separator" aria-orientation="vertical"
      aria-label={label} aria-valuenow={Math.round(handle.width)}
      aria-valuemin={handle.min} aria-valuemax={handle.max} tabIndex={0}
      onPointerDown={(e) => handle.onPointerDown(e, edge)}
      onPointerMove={handle.onPointerMove}
      onPointerUp={handle.end}
      onPointerCancel={handle.end}
      onKeyDown={(e) => handle.onKeyDown(e, edge)}>
      <span className="agent-resizer-grip" aria-hidden="true" />
    </div>
  );
}

