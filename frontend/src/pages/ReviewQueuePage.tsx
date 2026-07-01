import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError, type ReviewQueueItem } from '../api';
import NavBar from '../components/NavBar';

const formatSubmittedAt = (t: number | null): string => {
  if (t === null) return '—';
  try {
    return new Date(t * 1000).toLocaleString();
  } catch {
    return '—';
  }
};

/** Admin-only: sessions currently `submitted`, awaiting approve/send-back.
 * Opening one reuses the existing ChangesSummaryPage diff view (/admin/:sid). */
const ReviewQueuePage = () => {
  const [items, setItems] = useState<ReviewQueueItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .reviewQueue()
      .then((r) => {
        if (!cancelled) setItems(r);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.detail || e.code : 'Failed to load the review queue');
        setItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <NavBar title="Review queue" subtitle="Sessions submitted and awaiting your approval" />
      <main className="mx-auto max-w-review space-y-4 px-4 py-6">
        {items === null && <p className="text-gray-400">Loading…</p>}

        {items !== null && error && (
          <div className="rounded border border-red-700 bg-red-900/30 p-3 text-sm text-red-300">{error}</div>
        )}

        {items !== null && items.length === 0 && !error && (
          <p className="text-gray-400">Nothing awaiting approval right now.</p>
        )}

        {items !== null && items.length > 0 && (
          <ul className="divide-y divide-gray-700/60 overflow-hidden rounded-lg border border-gray-700 bg-gray-800/60">
            {items.map((it) => (
              <li key={it.sid} className="flex items-center justify-between gap-4 px-4 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="truncate text-sm text-gray-200">{it.title || it.trip_id}</p>
                    {it.edit_required && (
                      <span
                        className="rounded bg-amber-600 px-2 py-0.5 text-[11px] font-medium text-white"
                        title="A field is flagged edit-required"
                      >
                        Edit required
                      </span>
                    )}
                  </div>
                  <p className="truncate text-[11px] text-gray-500">
                    {it.trip_id} · {it.language} · submitted by {it.submitted_by ?? 'unknown'} ·{' '}
                    {formatSubmittedAt(it.submitted_at)}
                  </p>
                </div>
                <Link
                  to={`/admin/${it.sid}`}
                  className="shrink-0 rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
                >
                  Review
                </Link>
              </li>
            ))}
          </ul>
        )}
      </main>
    </>
  );
};

export default ReviewQueuePage;
