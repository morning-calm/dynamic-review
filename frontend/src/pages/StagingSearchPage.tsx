import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { toast } from 'react-toastify';
import { api, ApiError, type AdminStagingList, type SessionStatus } from '../api';
import NavBar from '../components/NavBar';

const STATUS_LABEL: Record<SessionStatus, string> = {
  in_review: 'In review',
  submitted: 'Submitted',
  approving: 'Approving…',
  approved: 'Approved',
  changes_requested: 'Changes requested',
  ai_review: 'AI review — with reviewer',
};

/** Admin-only staging-wide trip search (the Firefoo replacement's front door): find ANY
 * staging trip by id/title and open it in the normal session editor — including
 * completed trips (post-completion fixes) and trips on no Trello lane. */
const StagingSearchPage = () => {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [location, setLocation] = useState('');
  const [country, setCountry] = useState('');
  const [result, setResult] = useState<AdminStagingList | null>(null);
  const [loading, setLoading] = useState(false);
  const [opening, setOpening] = useState<string | null>(null);
  const debounce = useRef<number | undefined>(undefined);
  // Dropdown option lists are kept stable from the latest response — they shouldn't
  // shrink/reflow just because a filter narrowed the current result set.
  const [locations, setLocations] = useState<string[]>([]);
  const [countries, setCountries] = useState<string[]>([]);

  // Debounced search-as-you-type against the server's cached index.
  useEffect(() => {
    window.clearTimeout(debounce.current);
    debounce.current = window.setTimeout(() => {
      setLoading(true);
      api
        .adminStagingTrips(q, false, location, country)
        .then((r) => {
          setResult(r);
          setLocations(r.locations);
          setCountries(r.countries);
        })
        .catch((e: unknown) =>
          toast.error(`Search failed: ${e instanceof ApiError ? e.detail : 'network error'}`),
        )
        .finally(() => setLoading(false));
    }, 300);
    return () => window.clearTimeout(debounce.current);
  }, [q, location, country]);

  const open = (tripId: string) => {
    setOpening(tripId);
    api
      .adminOpenTrip(tripId)
      .then((session) => navigate(`/review/${session.id}`))
      .catch((e: unknown) => {
        toast.error(e instanceof ApiError ? e.detail || e.code : 'Could not open session');
        setOpening(null);
      });
  };

  return (
    <>
      <NavBar title="All staging trips" subtitle="Search & open any trip in staging Firebase (admin)" />
      <main className="mx-auto max-w-review space-y-4 px-4 py-6">
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search by trip id or title…"
            autoFocus
            className="w-full max-w-md rounded border border-gray-700 bg-gray-900 px-3 py-2 text-base sm:text-sm"
          />
          <select
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-base sm:text-sm"
          >
            <option value="">All countries</option>
            {countries.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <select
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-base sm:text-sm"
          >
            <option value="">All locations</option>
            {locations.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
          {result && (
            <span className="text-xs text-gray-500">
              {result.total} match{result.total === 1 ? '' : 'es'}
              {result.total > result.shown ? ` (showing ${result.shown} — narrow the search)` : ''}
            </span>
          )}
          {loading && <span className="text-xs text-gray-500">searching…</span>}
        </div>

        <p className="text-xs text-gray-500">
          Opening a trip seeds/resumes a normal review session (staging is the source of truth; nothing is
          written until approve). Completed trips open here too — this is the place for post-completion fixes.
        </p>

        {result && result.trips.length === 0 && <p className="text-gray-400">No staging trips match.</p>}

        {result && result.trips.length > 0 && (
          <ul className="divide-y divide-gray-700/60 overflow-hidden rounded-lg border border-gray-700 bg-gray-800/60">
            {result.trips.map((t) => (
              <li key={t.trip_id} className="flex flex-wrap items-center justify-between gap-4 gap-y-2 px-4 py-2.5">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="truncate text-sm text-gray-200">{t.title || t.trip_id}</p>
                    {t.status && (
                      <span className="rounded bg-gray-700 px-1.5 py-0.5 text-[11px] text-gray-300">
                        {STATUS_LABEL[t.status]}
                      </span>
                    )}
                    {t.completed_method && (
                      <span
                        className="rounded bg-emerald-800 px-1.5 py-0.5 text-[11px] text-emerald-200"
                        title={`Completed (${t.completed_method}) by ${t.completed_by ?? '—'}`}
                      >
                        completed
                      </span>
                    )}
                  </div>
                  <p className="truncate text-[11px] text-gray-500">
                    {t.trip_id} · {t.language}
                    {t.folder_name ? ` · ${t.folder_name}` : ''}
                    {t.location ? ` · ${t.location}` : ''}
                    {t.country ? ` · ${t.country}` : ''}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Link
                    to={`/structure/${encodeURIComponent(t.trip_id)}`}
                    className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700"
                    title="Edit scene structure (add/remove/reorder/swap) — direct staging writes"
                  >
                    Structure
                  </Link>
                  <button
                    type="button"
                    disabled={opening === t.trip_id}
                    onClick={() => open(t.trip_id)}
                    className="rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
                  >
                    {opening === t.trip_id ? 'Opening…' : t.has_session ? 'Resume' : 'Open'}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </main>
    </>
  );
};

export default StagingSearchPage;
