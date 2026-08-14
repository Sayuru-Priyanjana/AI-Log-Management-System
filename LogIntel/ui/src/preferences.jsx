import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { getSettings } from './api';

/**
 * Theme and time zone, for the whole app.
 *
 * The two belong together because both are answers to "how should this be
 * displayed", and both have to be settled before the first render: a theme
 * applied late flashes the wrong colours, and a time formatted late shifts
 * under the reader.
 *
 * The zone is the agent's, not the browser's. The agent renders times inside
 * its own prose — "the departure began at 10:42" — and if the page formatted in
 * a different zone the reader would hold two clocks at once. So the server's
 * value is the source of truth and this only mirrors it.
 */
const PreferencesContext = createContext(null);

const THEME_KEY = 'ui.theme';
const ZONE_KEY = 'ui.timezone';
const DEFAULT_ZONE = '+05:30';

function readTheme() {
  const stored = localStorage.getItem(THEME_KEY);
  if (stored === 'light' || stored === 'dark') return stored;
  // No stored choice means follow the operating system, which is what a user
  // who has never opened the toggle expects.
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function PreferencesProvider({ children }) {
  const [theme, setThemeState] = useState(readTheme);
  // Seeded from the last known value so the first paint is not in the wrong
  // zone while /api/settings is in flight.
  const [zone, setZoneState] = useState(() => localStorage.getItem(ZONE_KEY) || DEFAULT_ZONE);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
  }, [theme]);

  useEffect(() => {
    let mounted = true;
    if (!localStorage.getItem('jwt')) return;

    getSettings()
      .then((data) => {
        const value = data?.timezone?.value;
        if (mounted && value) {
          setZoneState(value);
          localStorage.setItem(ZONE_KEY, value);
        }
      })
      .catch(() => { /* the agent may be down; the cached zone still formats */ });
    return () => { mounted = false; };
  }, []);

  const setTheme = useCallback((next) => {
    setThemeState(next);
    localStorage.setItem(THEME_KEY, next);
  }, []);

  const setZone = useCallback((next) => {
    setZoneState(next);
    localStorage.setItem(ZONE_KEY, next);
  }, []);

  const value = useMemo(() => ({
    theme,
    setTheme,
    toggleTheme: () => setTheme(theme === 'dark' ? 'light' : 'dark'),
    zone,
    setZone,
    ...formatters(zone),
  }), [theme, setTheme, zone, setZone]);

  return (
    <PreferencesContext.Provider value={value}>{children}</PreferencesContext.Provider>
  );
}

// The provider and its hook belong in one file — splitting them puts the
// context object somewhere neither of them is. Fast refresh prefers one
// component per module; that trade is worth a slower reload in dev.
// eslint-disable-next-line react-refresh/only-export-components
export function usePreferences() {
  const context = useContext(PreferencesContext);
  if (!context) throw new Error('usePreferences must be used inside PreferencesProvider');
  return context;
}

/**
 * Formatters bound to one zone.
 *
 * Intl handles named zones; a fixed offset like "+05:30" is not a zone Intl
 * knows, so those are applied by arithmetic on the instant and then formatted
 * in UTC. Both paths take an ISO string in and give a wall-clock string out,
 * which is all any caller needs.
 */
function formatters(zone) {
  const named = zone && !/^[+-]\d{1,2}:?\d{2}$/.test(zone) && zone !== 'UTC';
  const offsetMinutes = named ? 0 : parseOffset(zone);

  const parts = (iso) => {
    if (!iso) return null;
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return null;
    if (named) {
      try {
        return new Intl.DateTimeFormat('en-GB', {
          timeZone: zone, hour12: false,
          year: 'numeric', month: '2-digit', day: '2-digit',
          hour: '2-digit', minute: '2-digit', second: '2-digit',
        }).formatToParts(date).reduce((acc, p) => ({ ...acc, [p.type]: p.value }), {});
      } catch {
        // An unknown zone name should not blank every timestamp on the page.
      }
    }
    const shifted = new Date(date.getTime() + offsetMinutes * 60000);
    return {
      year: String(shifted.getUTCFullYear()),
      month: pad(shifted.getUTCMonth() + 1),
      day: pad(shifted.getUTCDate()),
      hour: pad(shifted.getUTCHours()),
      minute: pad(shifted.getUTCMinutes()),
      second: pad(shifted.getUTCSeconds()),
    };
  };

  return {
    /** `10:42:00` — the common case, inside a row or a sentence. */
    formatClock: (iso, fallback = '—') => {
      const p = parts(iso);
      return p ? `${p.hour}:${p.minute}:${p.second}` : fallback;
    },
    /** `2026-08-12` */
    formatDay: (iso, fallback = '') => {
      const p = parts(iso);
      return p ? `${p.year}-${p.month}-${p.day}` : fallback;
    },
    /** `2026-08-12 10:42:00 +05:30` — where the date and zone both matter. */
    formatStamp: (iso, fallback = '—') => {
      const p = parts(iso);
      return p ? `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second} ${zone}` : fallback;
    },
    /** `2026-08-12T10:42` — for <input type="datetime-local"> */
    toInput: (iso) => {
      const p = parts(iso);
      return p ? `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}` : '';
    },
    /** Converts a <input type="datetime-local"> string back to a UTC ISO string */
    fromInput: (localString) => {
      if (!localString) return null;
      const fakeUTC = new Date(localString + 'Z');
      if (Number.isNaN(fakeUTC.getTime())) return null;
      
      if (!named) {
        return new Date(fakeUTC.getTime() - offsetMinutes * 60000).toISOString();
      } else {
        const p = parts(fakeUTC.toISOString());
        if (!p) return null;
        const formattedTime = new Date(`${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}Z`);
        const diff = formattedTime.getTime() - fakeUTC.getTime();
        return new Date(fakeUTC.getTime() - diff).toISOString();
      }
    },
    zoneLabel: zone,
  };
}

const pad = (n) => String(n).padStart(2, '0');

function parseOffset(zone) {
  const match = /^([+-])(\d{1,2}):?(\d{2})$/.exec(zone || '');
  if (!match) return 0;
  const minutes = Number(match[2]) * 60 + Number(match[3]);
  return match[1] === '-' ? -minutes : minutes;
}
