import { useState } from 'react';

/**
 * A titled section that folds away, with one affordance shared by all of them.
 *
 * The three big sections of an investigation — the signals, the evidence
 * timeline and the reasoning trace — had grown their own headers, their own
 * chevrons and their own defaults. Two opened expanded, which meant a finished
 * investigation rendered several screens of raw material above everything else
 * and the reader had to scroll past the working to reach the next question.
 *
 * They now default to collapsed and carry a one-line summary instead, so the
 * page reads as: what was asked, what the answer is, and what it rests on —
 * with the raw material one click away rather than always underfoot.
 */
export default function Collapsible({
  title, summary, right, children, defaultOpen = false, tone,
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className={`glass-panel li-section${tone ? ` li-section--${tone}` : ''}`}>
      <button type="button" className="li-section-head" onClick={() => setOpen(!open)}
        aria-expanded={open}>
        <Chevron open={open} />
        <span className="li-section-title">{title}</span>
        {summary && <span className="li-muted li-section-summary">{summary}</span>}
        <span className="li-spacer" />
        {right}
        {/* Spelled out rather than left to the chevron alone: "is there more
            here?" should be answerable without decoding an icon. */}
        <span className="li-section-hint">{open ? 'Hide' : 'Show'}</span>
      </button>

      {open && <div className="li-section-body">{children}</div>}
    </div>
  );
}

function Chevron({ open }) {
  return (
    <svg className="li-section-chevron" width="14" height="14" viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth="2.6" aria-hidden="true"
      style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .18s' }}>
      <path d="M9 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
