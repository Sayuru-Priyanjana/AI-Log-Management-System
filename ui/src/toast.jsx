import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';

/**
 * Transient messages, bottom-right.
 *
 * Replaces the inline banners that used to sit inside each page. Those took
 * permanent vertical space to say something temporary, pushed the content down
 * when they appeared, and were invisible if the thing that triggered them was
 * scrolled off. A toast costs no layout and lands in the same place every time.
 *
 * Errors do not auto-dismiss. Everything else does — a success message the user
 * has to click away is a second task handed back for doing the first one.
 */
const ToastContext = createContext(null);

let nextId = 1;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const timers = useRef(new Map());

  const dismiss = useCallback((id) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback((tone, message, options = {}) => {
    const id = nextId++;
    const ttl = options.ttl ?? (tone === 'error' ? 0 : 4500);
    setToasts((current) => {
      // The same failure twice is one failure. A poll that keeps failing, or a
      // React strict-mode double render, would otherwise pile up identical
      // rows that say nothing new and bury the ones that do.
      if (current.some((t) => t.message === message && t.detail === options.detail)) {
        return current;
      }
      return [...current.slice(-4), { id, tone, message, detail: options.detail }];
    });
    if (ttl > 0) {
      timers.current.set(id, setTimeout(() => dismiss(id), ttl));
    }
    return id;
  }, [dismiss]);

  const api = useMemo(() => ({
    success: (message, options) => push('success', message, options),
    error: (message, options) => push('error', message, options),
    info: (message, options) => push('info', message, options),
    dismiss,
  }), [push, dismiss]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast--${toast.tone}`}>
            <span className="toast-mark" aria-hidden="true">{MARK[toast.tone]}</span>
            <div className="toast-body">
              <div className="toast-message">{toast.message}</div>
              {toast.detail && <div className="toast-detail">{toast.detail}</div>}
            </div>
            <button type="button" className="toast-close" onClick={() => dismiss(toast.id)}
              aria-label="Dismiss">×</button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

const MARK = { success: '✓', error: '!', info: 'i' };

// eslint-disable-next-line react-refresh/only-export-components
export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error('useToast must be used inside ToastProvider');
  return context;
}
