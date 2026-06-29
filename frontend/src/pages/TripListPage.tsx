import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'react-toastify';
import { api, ApiError, type TripListItem } from '../api';

const StatusBadge = ({ trip }: { trip: TripListItem }) => {
  if (!trip.has_session) {
    return <span className="rounded bg-gray-700 px-2 py-0.5 text-xs text-gray-300">Not started</span>;
  }
  const submitted = trip.status === 'submitted';
  return (
    <span className={`rounded px-2 py-0.5 text-xs text-white ${submitted ? 'bg-blue-600' : 'bg-custom-green'}`}>
      {submitted ? 'Submitted' : 'In review'}
    </span>
  );
};

const TripListPage = () => {
  const navigate = useNavigate();
  const [trips, setTrips] = useState<TripListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [opening, setOpening] = useState<string | null>(null);

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

  return (
    <div className="mx-auto max-w-review px-4 py-8">
      <h1 className="mb-1 text-2xl font-semibold text-white">Trip review</h1>
      <p className="mb-6 text-sm text-gray-400">English `_EN` trips with local MP3 masters. Open one to review and correct.</p>

      {trips === null && <p className="text-gray-400">Loading trips…</p>}

      {trips !== null && error && (
        <div className="mb-4 rounded border border-red-700 bg-red-900/30 p-3 text-sm text-red-300">
          {error}. Is the backend running on 127.0.0.1:8000?
        </div>
      )}

      {trips !== null && trips.length === 0 && !error && <p className="text-gray-400">No trips found.</p>}

      <ul className="space-y-2">
        {trips?.map((trip) => (
          <li
            key={trip.trip_id}
            className="flex items-center justify-between gap-4 rounded-lg border border-gray-700 bg-gray-800/60 p-4"
          >
            <div className="min-w-0">
              <p className="truncate font-medium text-white">{trip.title || trip.trip_id}</p>
              <p className="truncate text-xs text-gray-400">{trip.folder_name}</p>
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <StatusBadge trip={trip} />
              <button
                type="button"
                disabled={opening === trip.trip_id}
                onClick={() => openTrip(trip.trip_id)}
                className="rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
              >
                {opening === trip.trip_id ? 'Opening…' : trip.has_session ? 'Resume' : 'Open'}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
};

export default TripListPage;
