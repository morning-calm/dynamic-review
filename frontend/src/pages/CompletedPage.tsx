import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { toast } from 'react-toastify';
import { api, ApiError, type CompletedItem, type CompletionMethod } from '../api';
import { useAuth } from '../authContext';
import NavBar from '../components/NavBar';

const formatCompletedAt = (t: number): string => {
  try {
    return new Date(t * 1000).toLocaleString();
  } catch {
    return '—';
  }
};

const METHOD_BADGE: Record<CompletionMethod, { label: string; cls: string }> = {
  approved: { label: 'Approved', cls: 'bg-emerald-700' },
  manual: { label: 'Manual', cls: 'bg-gray-600' },
};

/** Both roles: trips that are done — approved through the normal submit→approve
 * flow, or admin-marked-complete as a bypass for work already finished in the
 * old system. Reviewers see only their languages (server-filtered). Rows with a
 * session link to the existing read-only session view (/admin/:sid, same as
 * ReviewQueuePage); manual rows have no session to open. Only admins see the
 * "Un-complete" action. */
const CompletedPage = () => {
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const [items, setItems] = useState<CompletedItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Manual refresh after an action (no unmount-cancellation needed — one-shot).
  const load = () =>
    api
      .completed()
      .then((r) => setItems(r))
      .catch((e: unknown) => {
        setError(e instanceof ApiError ? e.detail || e.code : 'Failed to load completed trips');
        setItems([]);
      });

  useEffect(() => {
    let cancelled = false;
    api
      .completed()
      .then((r) => {
        if (!cancelled) setItems(r);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.detail || e.code : 'Failed to load completed trips');
        setItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const unComplete = (tripId: string) => {
    setBusyId(tripId);
    api
      .uncompleteTrip(tripId)
      .then(() => {
        toast.success('Un-completed — back on the main trip list.');
        return load();
      })
      .catch((e: unknown) => toast.error(`Un-complete failed: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setBusyId(null));
  };

  return (
    <>
      <NavBar title="Completed" subtitle="Approved trips and trips marked complete in the old system" />
      <main className="mx-auto max-w-review space-y-4 px-4 py-6">
        {items === null && <p className="text-gray-400">Loading…</p>}

        {items !== null && error && (
          <div className="rounded border border-red-700 bg-red-900/30 p-3 text-sm text-red-300">{error}</div>
        )}

        {items !== null && items.length === 0 && !error && <p className="text-gray-400">Nothing completed yet.</p>}

        {items !== null && items.length > 0 && (
          <ul className="divide-y divide-gray-700/60 overflow-hidden rounded-lg border border-gray-700 bg-gray-800/60">
            {items.map((it) => {
              const badge = METHOD_BADGE[it.method];
              return (
                <li key={it.trip_id} className="flex items-center justify-between gap-4 px-4 py-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="truncate text-sm text-gray-200">{it.title || it.trip_id}</p>
                      <span className={`rounded px-2 py-0.5 text-[11px] font-medium text-white ${badge.cls}`}>
                        {badge.label}
                      </span>
                    </div>
                    <p className="truncate text-[11px] text-gray-500">
                      {it.trip_id} · {it.language} · completed by {it.completed_by} · {formatCompletedAt(it.completed_at)}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    {it.session_id ? (
                      <Link
                        to={`/admin/${it.session_id}`}
                        className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-200 hover:bg-gray-700"
                      >
                        View
                      </Link>
                    ) : (
                      <span className="text-[11px] italic text-gray-500">Completed in old system</span>
                    )}
                    {isAdmin && (
                      <button
                        type="button"
                        disabled={busyId === it.trip_id}
                        onClick={() => unComplete(it.trip_id)}
                        className="rounded border border-red-700 px-3 py-1.5 text-sm text-red-400 hover:bg-red-900/30 disabled:opacity-50"
                      >
                        {busyId === it.trip_id ? 'Un-completing…' : 'Un-complete'}
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </main>
    </>
  );
};

export default CompletedPage;
