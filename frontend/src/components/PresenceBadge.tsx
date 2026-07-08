import type { PresenceEntry } from '../api';

const ago = (t: number): string => {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - t));
  if (s < 90) return 'now';
  return `${Math.round(s / 60)} min ago`;
};

/** Green live dots: who is on this trip/session right now and what they're doing
 * (from the presence heartbeat). Renders nothing when nobody is live. */
const PresenceBadge = ({ entries }: { entries: PresenceEntry[] }) => {
  if (entries.length === 0) return null;
  return (
    <span className="flex flex-wrap items-center gap-1">
      {entries.map((e) => (
        <span
          key={`${e.username}-${e.sid}`}
          className="inline-flex items-center gap-1 rounded border border-emerald-700/60 bg-emerald-900/40 px-1.5 py-0.5 text-[11px] text-emerald-300"
          title={`${e.username} (${e.role}) — ${e.context || 'active'} · ${ago(e.updated_at)}`}
        >
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
          {e.username}
          {e.context ? ` · ${e.context}` : ''}
        </span>
      ))}
    </span>
  );
};

export default PresenceBadge;
