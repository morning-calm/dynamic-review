import { useEffect, useState } from 'react';
import { api, type PresenceEntry } from './api';

/** FE ping cadence. The server treats a heartbeat within its live window (120s)
 * as "on the session", so 30s gives comfortable slack for a slow request. */
export const HEARTBEAT_MS = 30_000;

/**
 * Post a presence heartbeat every 30s while the calling page is mounted (plus one
 * immediately). Silent best-effort — presence must never break a review page.
 * An admin's heartbeat on a submitted session is what turns a reviewer's recall
 * into a request instead of a silent yank, so the admin pages MUST use this too.
 */
export const useHeartbeat = (sid: string | undefined, context: string): void => {
  useEffect(() => {
    if (!sid) return;
    let stopped = false;
    const ping = () => {
      if (!stopped) api.heartbeat(sid, context).catch(() => {});
    };
    ping();
    const t = setInterval(ping, HEARTBEAT_MS);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [sid, context]);
};

/** Poll the live-presence list (trip list / review queue dots). */
export const usePresence = (pollMs = 60_000): PresenceEntry[] => {
  const [entries, setEntries] = useState<PresenceEntry[]>([]);
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .presence()
        .then((p) => {
          if (!cancelled) setEntries(p);
        })
        .catch(() => {});
    load();
    const t = setInterval(load, pollMs);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [pollMs]);
  return entries;
};
