import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { toast } from 'react-toastify';
import { api, ApiError, isEditableStatus, type ExternalReport, type Field, type Session } from '../api';
import { SaveStatusProvider } from '../SaveStatusProvider';
import { useSaveCoordinator } from '../saveStatusContext';
import NavBar from '../components/NavBar';
import SaveStatus from '../components/SaveStatus';
import EditableField from '../components/EditableField';
import FlagControl from '../components/FlagControl';
import SceneCard from '../components/SceneCard';
import NarrationControls from '../components/NarrationControls';
import ZhFieldBlock from '../components/ZhFieldBlock';
import RecallControl from '../components/RecallControl';
import ExternalReports from '../components/ExternalReports';
import { useHeartbeat } from '../usePresence';

/** Scroll the first not-yet-done field into view (document order, read from the DOM
 * anchors FlagControl renders). Returns true if one was found. */
const jumpToFirstUndone = (): boolean => {
  const first = document.querySelector<HTMLElement>('[data-field-anchor][data-done="false"]');
  if (!first) return false;
  first.scrollIntoView({ behavior: 'smooth', block: 'center' });
  // brief flash so the reviewer's eye lands on the right block
  first.classList.add('ring-2', 'ring-amber-500');
  window.setTimeout(() => first.classList.remove('ring-2', 'ring-amber-500'), 1600);
  return true;
};

interface FieldLocation {
  trip: boolean;
  s: number; // scene array index (ignored when trip)
  i: number; // index within the field array
}

const ReviewBody = () => {
  const { sid = '' } = useParams<{ sid: string }>();
  const { state: saveState } = useSaveCoordinator();
  const navigate = useNavigate();

  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tripFields, setTripFields] = useState<Field[]>([]);
  const [sceneFields, setSceneFields] = useState<Field[][]>([]);
  const [submitting, setSubmitting] = useState(false);
  const locRef = useRef<Map<number, FieldLocation>>(new Map());

  // Every reviewable field across trip header + scenes, for the all-done gate.
  const allFields = useMemo(
    () => [...tripFields, ...sceneFields.flat()],
    [tripFields, sceneFields],
  );
  const remaining = useMemo(() => allFields.filter((f) => f.flag !== 'done').length, [allFields]);
  const allDone = allFields.length > 0 && remaining === 0;

  // Gate: jump to the first undone field, else submit. Used by both submit buttons.
  const handleSubmit = useCallback(() => {
    if (!allDone) {
      if (!jumpToFirstUndone()) return;
      toast.warn(`${remaining} section${remaining === 1 ? '' : 's'} still need listening to & marking done.`);
      return;
    }
    setSubmitting(true);
    api
      .submit(sid)
      .then((r) => {
        if (r.ok) {
          toast.success('Submitted for review.');
          navigate(`/admin/${sid}`);
        } else {
          toast.warn('Submit blocked by validation — see Changes & submit.');
          navigate(`/admin/${sid}`);
        }
      })
      .catch((e: unknown) => toast.error(`Submit failed: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setSubmitting(false));
  }, [allDone, remaining, sid, navigate]);

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

  // Re-fetch after a recall (the session just became editable again).
  const reload = useCallback(() => {
    api
      .getSession(sid)
      .then(applySession)
      .catch((e: unknown) => setError(e instanceof ApiError ? e.detail || e.code : 'Failed to load session'));
  }, [sid, applySession]);

  // Presence heartbeat: who's on this session and what they're doing (trip list /
  // queue live dots; also the recall "admin mid-review" signal on the admin pages).
  useHeartbeat(
    session ? sid : undefined,
    session && isEditableStatus(session.status) ? 'editing' : 'viewing (locked)',
  );

  // Stage-4b field reports from the web/VR apps (refresh=true re-syncs from staging).
  const [extReports, setExtReports] = useState<ExternalReport[]>([]);
  useEffect(() => {
    let cancelled = false;
    api
      .externalReports(sid, true)
      .then((r) => {
        if (!cancelled) setExtReports(r.reports);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [sid]);
  const updateExtReport = useCallback(
    (r: ExternalReport) => setExtReports((prev) => prev.map((o) => (o.id === r.id ? r : o))),
    [],
  );

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
  // Locked (read-only) once submitted — the backend 403s edit/regenerate/flag
  // endpoints in that window too, so this just keeps the UI honest about it.
  const editable = isEditableStatus(session.status);
  // Mandarin A/B-audition mode (review-app-chinese-review.md): 4-script editing +
  // V2/V3 players instead of the splice/regenerate/coverage flow. Every other
  // language takes the branches below exactly as before.
  const isZh = session.is_zh;

  return (
    <>
      <NavBar
        title={session.trip_id}
        subtitle={`${session.folder_name} · voice: ${session.voice_display}${
          isZh ? ' · V3 audio' : ''
        }`}
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
        {session.status === 'changes_requested' && (
          <div className="rounded border border-amber-700 bg-amber-900/20 p-3 text-sm text-amber-200">
            <p className="font-medium">Changes requested by the admin.</p>
            {session.review_note && <p className="mt-1 whitespace-pre-wrap text-amber-100">{session.review_note}</p>}
            <p className="mt-1 text-amber-300/80">Make the changes below, then submit for review again.</p>
          </div>
        )}

        {(session.status === 'submitted' || session.status === 'approving') && (
          <div className="rounded border border-blue-700 bg-blue-900/30 p-3 text-sm text-blue-200">
            {session.status === 'approving'
              ? 'Approval in progress…'
              : 'Submitted for review — awaiting admin approval. The content below is read-only until it’s approved or sent back.'}
          </div>
        )}

        {session.status === 'approved' && (
          <div className="rounded border border-emerald-700 bg-emerald-900/20 p-3 text-sm text-emerald-200">
            Approved{session.approved_by ? ` by ${session.approved_by}` : ''} — awaiting Stage 9 finalise. Further
            edits would start a new round of corrections in a fresh session.
          </div>
        )}

        {/* Recall submission: auto-grant when possible, else a reasoned admin request. */}
        <RecallControl session={session} onChanged={reload} />

        {!isZh && <NarrationControls session={session} onUpdate={applySession} />}

        {/* Trip header */}
        <section className="space-y-4 rounded-lg border border-gray-700 bg-gray-800/60 p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">Trip</h2>
          {contentTitleKey && (
            isZh ? (
              <ZhFieldBlock
                field={contentTitleKey}
                sid={session.id}
                onFieldUpdate={updateField}
                label="Display title (contentTitleKey)"
                singleLine
                readOnly={!editable}
              />
            ) : (
              <div className="space-y-2" inert={!editable}>
                <EditableField field={contentTitleKey} sid={session.id} onFieldUpdate={updateField} label="Display title (contentTitleKey)" singleLine />
                <FlagControl field={contentTitleKey} sid={session.id} onFieldUpdate={updateField} />
              </div>
            )
          )}
          {tripDescription && (
            isZh ? (
              <ZhFieldBlock
                field={tripDescription}
                sid={session.id}
                onFieldUpdate={updateField}
                label="TripGroup description"
                rows={4}
                readOnly={!editable}
              />
            ) : (
              <div className="space-y-2" inert={!editable}>
                <EditableField
                  field={tripDescription}
                  sid={session.id}
                  onFieldUpdate={updateField}
                  label="TripGroup description"
                  rows={4}
                />
                <FlagControl field={tripDescription} sid={session.id} onFieldUpdate={updateField} />
              </div>
            )
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

        {/* Trip-level field reports (no scene index) */}
        <ExternalReports reports={extReports.filter((r) => r.scene_index === null)} onUpdate={updateExtReport} />

        {/* Scenes */}
        {session.scenes.map((scene, si) => (
          <div key={scene.index} className="space-y-2">
            <ExternalReports
              reports={extReports.filter((r) => r.scene_index === scene.index)}
              onUpdate={updateExtReport}
            />
            <SceneCard
              scene={scene}
              fields={sceneFields[si] ?? scene.fields}
              sid={session.id}
              onFieldUpdate={updateField}
              readOnly={!editable}
              isZh={isZh}
              language={session.language}
            />
          </div>
        ))}

        {/* Bottom submit — only submits once every section is listened-to & done;
            otherwise it jumps to the first section still needing review. Hidden
            once locked (submitted/approving/approved) — the banner above covers it. */}
        {editable && (
          <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm text-gray-300">
                {allDone ? (
                  <span className="text-custom-green">All sections done — ready to submit.</span>
                ) : (
                  <span>
                    <span className="font-medium text-amber-400">{remaining}</span> section
                    {remaining === 1 ? '' : 's'} still need listening to &amp; marking done.
                  </span>
                )}
              </p>
              <button
                type="button"
                disabled={submitting}
                onClick={handleSubmit}
                title={allDone ? 'Submit for review' : 'Jump to the first section not yet marked done'}
                className={`rounded px-4 py-2 text-sm font-medium text-white disabled:opacity-50 ${
                  allDone ? 'bg-custom-green hover:opacity-90' : 'bg-amber-600 hover:opacity-90'
                }`}
              >
                {submitting ? 'Submitting…' : allDone ? 'Submit for review' : 'Review remaining'}
              </button>
            </div>
          </section>
        )}
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
