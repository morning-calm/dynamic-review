import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { toast } from 'react-toastify';
import { api, ApiError, type FinalCheckList } from '../api';
import NavBar from '../components/NavBar';

const errText = (e: unknown, fallback: string): string =>
  e instanceof ApiError ? e.detail || e.code : fallback;

const LANE_LABEL: Record<string, string> = {
  '10': '10 · Final VR check',
  '10b': '10b · Recall quizzes',
  '11': '11 · Ready to publish',
  manual: 'manual',
};

/** Admin work list for the post-approval Final check (Trello lanes 10/10b/11) —
 * one row per trip; family-level checks are shared across siblings, so later
 * rungs of a family arrive mostly green. */
const FinalCheckListPage = () => {
  const [data, setData] = useState<FinalCheckList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .listFinalChecks()
      .then(setData)
      .catch((e: unknown) => setError(errText(e, 'Failed to load')));
  }, []);

  useEffect(load, [load]);

  const start = (tripId: string) => {
    setBusy(tripId);
    api
      .startFinalCheck(tripId)
      .then(() => {
        toast.success(`${tripId} added to the release-prep list`);
        load();
      })
      .catch((e: unknown) => toast.error(errText(e, 'Failed to start')))
      .finally(() => setBusy(null));
  };

  return (
    <div className="min-h-screen">
      <NavBar title="Release prep" subtitle="Post-approval checks before publish (lanes 10–11)" />
      <main className="mx-auto max-w-review space-y-6 px-4 py-6">
        {error && <p className="text-rose-400">{error}</p>}
        {!data && !error && <p className="text-gray-400">Loading…</p>}
        {data && (
          <>
            {!data.manifest_has_final && (
              <div className="rounded-lg border border-amber-800 bg-amber-900/20 p-3 text-sm text-amber-100">
                The manifest carries no release-prep lanes yet — re-run{' '}
                <code className="text-amber-200">Trello/export_review_trips.py</code> on the
                workstation (and pull here) to mirror the lane-10/10b/11 cards.
              </div>
            )}

            <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
              <h2 className="mb-3 text-sm font-semibold text-white">
                Trips in release prep{' '}
                <span className="font-normal text-gray-400">({data.items.length})</span>
              </h2>
              {data.items.length === 0 && (
                <p className="text-sm text-gray-500">Nothing in lanes 10/10b/11.</p>
              )}
              <ul className="divide-y divide-gray-700/60">
                {data.items.map((r) => (
                  <li key={r.trip_id}>
                    <Link
                      to={`/final-check/${encodeURIComponent(r.trip_id)}`}
                      className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 hover:bg-gray-700/30"
                    >
                      <span className="font-medium text-gray-100">{r.trip_id}</span>
                      <span className="rounded bg-gray-700 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-gray-300">
                        {LANE_LABEL[r.lane] ?? r.lane}
                      </span>
                      {r.language && <span className="text-xs text-gray-400">{r.language}</span>}
                      {r.pending_delta && (
                        <span
                          className="rounded bg-sky-900/70 px-1.5 py-0.5 text-[10px] uppercase text-sky-200"
                          title="Changed clips await a delta re-review — the final check is on hold until the reviewer approves them"
                        >
                          back in review
                        </span>
                      )}
                      {!r.tg_resolved && (
                        <span
                          className="rounded bg-rose-900/60 px-1.5 py-0.5 text-[10px] uppercase text-rose-200"
                          title="No staging TripGroup lists this trip — group-level checks fall back to the naive id"
                        >
                          no TripGroup
                        </span>
                      )}
                      <span className="ml-auto text-xs tabular-nums">
                        <span className={r.done === r.total ? 'text-emerald-400' : 'text-gray-300'}>
                          {r.done}/{r.total}
                        </span>{' '}
                        <span className="text-gray-500">checks</span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>

            {data.audit.length > 0 && (
              <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
                <h2 className="mb-1 text-sm font-semibold text-white">
                  Completed, but on no release-prep card
                </h2>
                <p className="mb-3 text-xs text-gray-400">
                  Review finished here, yet the Trello card isn’t in lanes 10–11. Move the card,
                  or start release prep by hand for a one-off.
                </p>
                <ul className="divide-y divide-gray-700/60">
                  {data.audit.map((a) => (
                    <li key={a.trip_id} className="flex items-center gap-3 py-2">
                      <span className="text-sm text-gray-200">{a.trip_id}</span>
                      <span className="text-xs text-gray-500">
                        {a.method} · {new Date(a.completed_at * 1000).toLocaleDateString()}
                        {a.card_lane && ` · card in lane ${a.card_lane}`}
                      </span>
                      <button
                        type="button"
                        disabled={busy === a.trip_id}
                        onClick={() => start(a.trip_id)}
                        className="ml-auto rounded border border-gray-600 px-2 py-1 text-xs text-gray-200 hover:bg-gray-700 disabled:opacity-50"
                      >
                        Start release prep
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
};

export default FinalCheckListPage;
