import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { toast } from 'react-toastify';
import { api, ApiError, type Field, type Session } from '../api';
import { SaveStatusProvider } from '../SaveStatusProvider';
import { useSaveCoordinator } from '../saveStatusContext';
import NavBar from '../components/NavBar';
import SaveStatus from '../components/SaveStatus';
import EditableField from '../components/EditableField';
import SceneCard from '../components/SceneCard';
import NarrationControls from '../components/NarrationControls';

interface FieldLocation {
  trip: boolean;
  s: number; // scene array index (ignored when trip)
  i: number; // index within the field array
}

const ReviewBody = () => {
  const { sid = '' } = useParams<{ sid: string }>();
  const { state: saveState } = useSaveCoordinator();

  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tripFields, setTripFields] = useState<Field[]>([]);
  const [sceneFields, setSceneFields] = useState<Field[][]>([]);
  const locRef = useRef<Map<number, FieldLocation>>(new Map());

  // Apply a whole Session payload (initial load + after a narration change, which can
  // reset many fields at once): refresh state and rebuild the fid→location index.
  const applySession = useCallback((s: Session) => {
    setSession(s);
    setTripFields(s.trip_fields);
    setSceneFields(s.scenes.map((sc) => sc.fields));
    const loc = new Map<number, FieldLocation>();
    s.trip_fields.forEach((f, i) => loc.set(f.fid, { trip: true, s: -1, i }));
    s.scenes.forEach((sc, si) => sc.fields.forEach((f, fi) => loc.set(f.fid, { trip: false, s: si, i: fi })));
    locRef.current = loc;
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .getSession(sid)
      .then((s) => {
        if (!cancelled) applySession(s);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof ApiError ? e.detail || e.code : 'Failed to load session');
      });
    return () => {
      cancelled = true;
    };
  }, [sid, applySession]);

  // Stable updater. Replaces only the changed field's array entry so unchanged
  // scene arrays keep their reference and memoised SceneCards skip re-rendering.
  const updateField = useCallback((f: Field) => {
    const loc = locRef.current.get(f.fid);
    if (!loc) return;
    if (loc.trip) {
      setTripFields((prev) => prev.map((old, i) => (i === loc.i ? f : old)));
    } else {
      setSceneFields((prev) => prev.map((arr, s) => (s === loc.s ? arr.map((old, i) => (i === loc.i ? f : old)) : arr)));
    }
  }, []);

  if (error) {
    return (
      <div className="mx-auto max-w-review px-4 py-8">
        <Link to="/" className="text-sm text-custom-green hover:underline">
          ← Trips
        </Link>
        <p className="mt-4 rounded border border-red-700 bg-red-900/30 p-3 text-sm text-red-300">{error}</p>
      </div>
    );
  }

  if (!session) {
    return <p className="mx-auto max-w-review px-4 py-8 text-gray-400">Loading session…</p>;
  }

  const contentTitleKey = tripFields.find((f) => f.field_path === 'contentTitleKey');
  const tripDescription = tripFields.find((f) => f.field_path === 'tripgroup_description');

  return (
    <>
      <NavBar
        title={session.trip_id}
        subtitle={`${session.folder_name} · voice: ${session.voice_display}`}
        right={
          <>
            <SaveStatus state={saveState} />
            <Link
              to={`/admin/${session.id}`}
              className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-200 hover:bg-gray-700"
            >
              Changes & submit
            </Link>
          </>
        }
      />

      <main className="mx-auto max-w-review space-y-6 px-4 py-6">
        {session.status === 'submitted' && (
          <div className="rounded border border-blue-700 bg-blue-900/30 p-3 text-sm text-blue-200">
            This session has been submitted — awaiting Stage 9 finalise. Further edits create a new round of corrections.
          </div>
        )}

        <NarrationControls session={session} onUpdate={applySession} />

        {/* Trip header */}
        <section className="space-y-4 rounded-lg border border-gray-700 bg-gray-800/60 p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">Trip</h2>
          {contentTitleKey && (
            <EditableField field={contentTitleKey} sid={session.id} onFieldUpdate={updateField} label="Display title (contentTitleKey)" singleLine />
          )}
          {tripDescription && (
            <EditableField
              field={tripDescription}
              sid={session.id}
              onFieldUpdate={updateField}
              label="TripGroup description"
              rows={4}
            />
          )}
          <div>
            <p className="mb-1 text-xs font-medium uppercase tracking-wide text-gray-400">Trip categories (read-only)</p>
            <div className="flex flex-wrap gap-2">
              {session.trip_categories.length === 0 && <span className="text-xs text-gray-500">none</span>}
              {session.trip_categories.map((c) => (
                <span key={c} className="rounded bg-gray-700 px-2 py-0.5 text-xs text-gray-200">
                  {c}
                </span>
              ))}
            </div>
          </div>
        </section>

        {/* Scenes */}
        {session.scenes.map((scene, si) => (
          <SceneCard
            key={scene.index}
            scene={scene}
            fields={sceneFields[si] ?? scene.fields}
            sid={session.id}
            onFieldUpdate={updateField}
          />
        ))}
      </main>
    </>
  );
};

const ReviewPage = () => {
  // Validate sid presence early so toasts are meaningful.
  const { sid } = useParams<{ sid: string }>();
  useEffect(() => {
    if (!sid) toast.error('Missing session id');
  }, [sid]);
  return (
    <SaveStatusProvider>
      <ReviewBody />
    </SaveStatusProvider>
  );
};

export default ReviewPage;
