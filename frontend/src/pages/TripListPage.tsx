import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Modal from 'react-modal';
import { toast } from 'react-toastify';
import { api, ApiError, type SessionStatus, type TripListItem } from '../api';
import { useAuth } from '../authContext';
import NavBar from '../components/NavBar';
import PresenceBadge from '../components/PresenceBadge';
import { usePresence } from '../usePresence';

type LaneFilter = 'all' | '6' | '7';

const MODAL_STYLE: Modal.Styles = {
  overlay: { backgroundColor: 'rgba(0,0,0,0.6)', zIndex: 50 },
  content: {
    inset: '50% auto auto 50%',
    transform: 'translate(-50%,-50%)',
    maxWidth: '480px',
    width: '90%',
    background: '#111827',
    border: '1px solid #374151',
    borderRadius: '0.5rem',
    padding: '1rem',
    color: 'white',
    maxHeight: '85vh',
    overflow: 'auto',
  },
};

// Variant display order within a family.
const LEVEL_ORDER = ['EN', 'A12', 'B1', 'B2', 'N5', 'N4', 'HSK1-2', 'HSK3', 'ZH', 'JP', ''];
const levelRank = (l: string): number => {
  const i = LEVEL_ORDER.indexOf(l);
  return i < 0 ? 99 : i;
};

const STATUS_BADGE: Record<SessionStatus, { label: string; cls: string }> = {
  in_review: { label: 'In review', cls: 'bg-custom-green' },
  submitted: { label: 'Submitted', cls: 'bg-blue-600' },
  approving: { label: 'Approving…', cls: 'bg-blue-500' },
  approved: { label: 'Approved', cls: 'bg-emerald-700' },
  changes_requested: { label: 'Changes requested', cls: 'bg-amber-600' },
  ai_review: { label: 'AI review — respond', cls: 'bg-purple-600' },
};

const StatusBadge = ({ trip }: { trip: TripListItem }) => {
  if (!trip.has_session || !trip.status) {
    return <span className="rounded bg-gray-700 px-2 py-0.5 text-xs text-gray-300">Not started</span>;
  }
  const badge = STATUS_BADGE[trip.status];
  return (
    <span className="flex items-center gap-1.5">
      <span className={`rounded px-2 py-0.5 text-xs text-white ${badge.cls}`}>{badge.label}</span>
      {trip.edit_required && (
        <span className="rounded bg-amber-600 px-2 py-0.5 text-xs font-medium text-white" title="A field is flagged edit-required">
          Edit required
        </span>
      )}
    </span>
  );
};

const TripListPage = () => {
  const navigate = useNavigate();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const [trips, setTrips] = useState<TripListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  const [lane, setLane] = useState<LaneFilter>('all');
  // Live presence dots: who is on which trip right now (heartbeat from the session pages).
  const presence = usePresence();

  // Mark-complete modal (admin only): the trip pending confirmation + its optional note.
  const [completeTarget, setCompleteTarget] = useState<TripListItem | null>(null);
  const [completeNote, setCompleteNote] = useState('');
  const [completing, setCompleting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .listTrips()
      .then((t) => {
        if (!cancelled) setTrips(t);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const msg = e instanceof ApiError ? e.detail || e.code : 'Failed to load trips';
        setError(msg);
        setTrips([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Manual refresh after a mark-complete action (no unmount-cancellation needed — one-shot).
  const refreshTrips = () =>
    api
      .listTrips()
      .then((t) => setTrips(t))
      .catch((e: unknown) => {
        const msg = e instanceof ApiError ? e.detail || e.code : 'Failed to load trips';
        setError(msg);
        setTrips([]);
      });

  const togglePin = (trip: TripListItem) => {
    (trip.pinned ? api.unpinTrip(trip.trip_id) : api.pinTrip(trip.trip_id))
      .then(refreshTrips)
      .catch((e: unknown) => toast.error(`Pin failed: ${e instanceof ApiError ? e.detail : 'network error'}`));
  };

  const openCompleteModal = (trip: TripListItem) => {
    setCompleteNote('');
    setCompleteTarget(trip);
  };

  const confirmComplete = () => {
    if (!completeTarget) return;
    const target = completeTarget;
    setCompleting(true);
    api
      .completeTrip(target.trip_id, completeNote.trim() || undefined)
      .then(() => {
        toast.success(`Marked "${target.title || target.trip_id}" complete.`);
        setCompleteTarget(null);
        setCompleteNote('');
        return refreshTrips();
      })
      .catch((e: unknown) => toast.error(`Mark complete failed: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setCompleting(false));
  };

  const laneCounts = useMemo(() => {
    const c = { '6': 0, '7': 0 };
    for (const t of trips ?? []) if (t.lane === '6' || t.lane === '7') c[t.lane] += 1;
    return c;
  }, [trips]);

  // Filter by lane, then group by family (place). Priority comes from the SERVER order
  // (pinned trips first, then Trello card order), so groups are ordered by their topmost
  // server position and pinned variants float to the top within a group.
  const groups = useMemo(() => {
    if (!trips) return [];
    const serverIndex = new Map(trips.map((t, i) => [t.trip_id, i] as const));
    const filtered = lane === 'all' ? trips : trips.filter((t) => t.lane === lane);
    const byFamily = new Map<string, TripListItem[]>();
    for (const t of filtered) {
      const key = t.family || t.trip_id;
      const arr = byFamily.get(key);
      if (arr) arr.push(t);
      else byFamily.set(key, [t]);
    }
    return [...byFamily.entries()]
      .map(([family, items]) => ({
        family,
        items: [...items].sort(
          (a, b) =>
            Number(b.pinned) - Number(a.pinned) ||
            levelRank(a.level) - levelRank(b.level) ||
            a.trip_id.localeCompare(b.trip_id),
        ),
        rank: Math.min(...items.map((t) => serverIndex.get(t.trip_id) ?? Number.MAX_SAFE_INTEGER)),
      }))
      .sort((a, b) => a.rank - b.rank);
  }, [trips, lane]);

  const openTrip = (tripId: string) => {
    setOpening(tripId);
    api
      .createOrResumeSession(tripId)
      .then((session) => navigate(`/review/${session.id}`))
      .catch((e: unknown) => {
        const msg = e instanceof ApiError ? e.detail || e.code : 'Could not open session';
        toast.error(msg);
        setOpening(null);
      });
  };

  const tabs: [LaneFilter, string][] = [
    ['all', 'All'],
    ['6', `Translator · 6 (${laneCounts['6']})`],
    ['7', `KP confirm · 7 (${laneCounts['7']})`],
  ];

  return (
    // The trip list used to hand-roll its own heading + UserMenu, so it was the one page
    // whose top bar scrolled away. It now uses the same sticky <NavBar> as everywhere
    // else (no back link — this IS the back link's destination).
    <>
      <NavBar title="Trip review" backTo={null} />
      <div className="mx-auto max-w-review px-4 py-8">
        <p className="mb-4 text-sm text-gray-400">
          Trips in Trello lanes 6 (translator review) &amp; 7 (KP confirm), grouped by place. Open a reviewable
          variant to correct.
        </p>

      <div className="mb-5 flex gap-2">
        {tabs.map(([v, label]) => (
          <button
            key={v}
            type="button"
            onClick={() => setLane(v)}
            className={`rounded px-3 py-1 text-sm ${
              lane === v ? 'bg-custom-green text-white' : 'border border-gray-600 text-gray-300 hover:bg-gray-700'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {trips === null && <p className="text-gray-400">Loading trips…</p>}

      {trips !== null && error && (
        <div className="mb-4 rounded border border-red-700 bg-red-900/30 p-3 text-sm text-red-300">
          {error}. Is the backend running on 127.0.0.1:8000?
        </div>
      )}

      {trips !== null && groups.length === 0 && !error && <p className="text-gray-400">No trips in this lane.</p>}

      <ul className="space-y-3">
        {groups.map((g) => (
          <li key={g.family} className="overflow-hidden rounded-lg border border-gray-700 bg-gray-800/60">
            <div className="flex items-center justify-between border-b border-gray-700 px-4 py-2">
              <span className="text-sm font-medium text-white">{g.family.replace(/_/g, ' ')}</span>
              <span className="text-[11px] text-gray-500">
                {g.items.length} variant{g.items.length === 1 ? '' : 's'}
              </span>
            </div>
            <ul className="divide-y divide-gray-700/60">
              {g.items.map((trip) => (
                <li key={trip.trip_id} className="flex flex-wrap items-center justify-between gap-4 gap-y-2 px-4 py-2.5">
                  <div className="flex min-w-0 items-center gap-2">
                    {trip.pinned && (
                      <span className="shrink-0 text-amber-400" title="Pinned to top">📌</span>
                    )}
                    <span className="shrink-0 rounded bg-sky-900/50 px-1.5 py-0.5 text-[11px] font-medium text-sky-300">
                      {trip.level || '—'}
                    </span>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="truncate text-sm text-gray-200">{trip.title || trip.trip_id}</p>
                        <PresenceBadge entries={presence.filter((p) => p.trip_id === trip.trip_id)} />
                      </div>
                      <p className="truncate text-[11px] text-gray-500">{trip.trip_id}</p>
                    </div>
                  </div>
                  <div className="flex w-full flex-wrap items-center gap-2 gap-y-2 sm:w-auto sm:shrink-0 sm:justify-end sm:gap-3">
                    {trip.lane && (
                      <span className="rounded bg-gray-700 px-2 py-0.5 text-[11px] text-gray-300">Lane {trip.lane}</span>
                    )}
                    {!trip.reviewable && <span className="text-[11px] text-amber-400/80">no local audio</span>}
                    <StatusBadge trip={trip} />
                    {isAdmin && (
                      <button
                        type="button"
                        onClick={() => togglePin(trip)}
                        className={`rounded border px-2 py-1 text-xs ${
                          trip.pinned
                            ? 'border-amber-500 text-amber-300 hover:bg-amber-900/30'
                            : 'border-gray-600 text-gray-300 hover:bg-gray-700'
                        }`}
                        title={trip.pinned ? 'Unpin from top' : 'Pin to top of the reviewer list'}
                      >
                        {trip.pinned ? 'Unpin' : 'Pin'}
                      </button>
                    )}
                    {isAdmin && (
                      <button
                        type="button"
                        onClick={() => openCompleteModal(trip)}
                        className="rounded border border-gray-600 px-2 py-1 text-xs text-gray-300 hover:bg-gray-700"
                      >
                        Mark complete
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={opening === trip.trip_id || !trip.reviewable}
                      onClick={() => openTrip(trip.trip_id)}
                      className="rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
                      title={!trip.reviewable ? 'No local audio for this trip yet' : undefined}
                    >
                      {opening === trip.trip_id ? 'Opening…' : trip.has_session ? 'Resume' : 'Open'}
                    </button>
                    {/* The disabled-state explanation lives in the title tooltip, which
                        touch devices never show — surface it as text on phones. */}
                    {!trip.reviewable && (
                      <span className="text-[11px] text-gray-500 sm:hidden">no audio yet</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>

      <Modal
        isOpen={completeTarget !== null}
        onRequestClose={() => !completing && setCompleteTarget(null)}
        style={MODAL_STYLE}
        contentLabel="Mark trip complete"
      >
        <h2 className="mb-2 text-sm font-semibold">Mark complete</h2>
        <p className="mb-3 text-xs text-gray-400">
          Marks <span className="text-gray-200">{completeTarget?.title || completeTarget?.trip_id}</span> complete
          without a review session — for work already finished in the old system. It leaves the main list; an admin
          can un-complete it later from the Completed page. This does not write staging or promote audio.
        </p>
        <textarea
          value={completeNote}
          onChange={(e) => setCompleteNote(e.target.value)}
          placeholder="Optional note (visible to admins)"
          rows={3}
          autoFocus
          className="mb-3 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-base sm:text-sm"
        />
        <div className="flex justify-end gap-2">
          <button
            type="button"
            disabled={completing}
            onClick={() => setCompleteTarget(null)}
            className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={completing}
            onClick={confirmComplete}
            className="rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {completing ? 'Marking…' : 'Mark complete'}
          </button>
        </div>
      </Modal>
      </div>
    </>
  );
};

export default TripListPage;
