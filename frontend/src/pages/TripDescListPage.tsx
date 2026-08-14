import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError, type TripDescItem, type TripDescStatus } from '../api';
import { useAuth } from '../authContext';
import NavBar from '../components/NavBar';

const STATUS_BADGE: Record<TripDescStatus, { label: string; cls: string }> = {
  pending_en: { label: 'English check', cls: 'bg-purple-700' },
  translating: { label: 'Translating…', cls: 'bg-sky-800' },
  pending_tl: { label: 'Translator review', cls: 'bg-amber-700' },
  done: { label: 'Done', cls: 'bg-emerald-700' },
};

/** Family-level trip-description review queue. Admins see every family from the
 * review manifest (the list lazily seeds new ones); reviewers see only their
 * pending translation items. */
const TripDescListPage = () => {
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const [items, setItems] = useState<TripDescItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hideDone, setHideDone] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api
      .listTripDescs()
      .then((r) => {
        if (!cancelled) setItems(r.items);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.detail || e.code : 'Failed to load descriptions');
        setItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const shown = (items ?? []).filter((i) => !hideDone || i.status !== 'done');

  return (
    <div className="min-h-screen">
      <NavBar
        title="Trip descriptions"
        subtitle={
          isAdmin
            ? 'Family-level descriptions: check the English + categories, then each translation'
            : 'Trip descriptions waiting for your translation review'
        }
      />
      <main className="mx-auto max-w-review px-4 py-6">
        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}
        {items === null && <p className="text-gray-400">Loading…</p>}
        {items !== null && shown.length === 0 && (
          <p className="text-gray-400">Nothing waiting here.</p>
        )}
        {isAdmin && items !== null && items.some((i) => i.status === 'done') && (
          <label className="mb-4 flex items-center gap-2 text-xs text-gray-400">
            <input
              type="checkbox"
              checked={hideDone}
              onChange={(e) => setHideDone(e.target.checked)}
            />
            Hide completed
          </label>
        )}
        <ul className="space-y-2">
          {shown.map((i) => {
            const badge = STATUS_BADGE[i.status];
            return (
              <li key={i.tg_id}>
                <Link
                  to={`/descriptions/${encodeURIComponent(i.tg_id)}`}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-gray-700 bg-gray-800/60 px-4 py-3 hover:border-gray-500"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium text-white">{i.family || i.tg_id}</span>
                    <span className="block truncate text-xs text-gray-400">
                      {i.tg_id} · {i.language}
                    </span>
                  </span>
                  <span className="flex items-center gap-2">
                    {i.last_error && (
                      <span
                        className="rounded bg-rose-700 px-2 py-0.5 text-xs text-white"
                        title={i.last_error}
                      >
                        Translation failed
                      </span>
                    )}
                    <span className={`rounded px-2 py-0.5 text-xs text-white ${badge.cls}`}>
                      {badge.label}
                    </span>
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      </main>
    </div>
  );
};

export default TripDescListPage;
