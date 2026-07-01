import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import Modal from 'react-modal';
import { toast } from 'react-toastify';
import {
  api,
  ApiError,
  isEditableStatus,
  type ApproveResponse,
  type Field,
  type Session,
  type SubmitResponse,
} from '../api';
import { useAuth } from '../authContext';
import NavBar from '../components/NavBar';
import InlineDiff from '../components/InlineDiff';

interface FlatField {
  field: Field;
  sceneIndex: number | null;
}

type ResultState = { kind: 'submit'; data: SubmitResponse } | { kind: 'approve'; data: ApproveResponse } | null;

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
  },
};

const replaceField = (s: Session, f: Field): Session => ({
  ...s,
  trip_fields: s.trip_fields.map((o) => (o.fid === f.fid ? f : o)),
  scenes: s.scenes.map((sc) => ({ ...sc, fields: sc.fields.map((o) => (o.fid === f.fid ? f : o)) })),
});

const flatten = (s: Session): FlatField[] => {
  const out: FlatField[] = s.trip_fields.map((field) => ({ field, sceneIndex: null }));
  s.scenes.forEach((sc) => sc.fields.forEach((field) => out.push({ field, sceneIndex: sc.index })));
  return out;
};

const fieldLabel = (ff: FlatField): string =>
  ff.sceneIndex === null ? ff.field.field_path : `Scene ${ff.sceneIndex} · ${ff.field.field_path}`;

const ImportMp3 = ({ field, sid, onUpdate }: { field: Field; sid: string; onUpdate: (f: Field) => void }) => {
  const [busy, setBusy] = useState(false);
  return (
    <label className={`inline-flex cursor-pointer items-center rounded border border-gray-600 px-2 py-1 text-xs text-gray-200 hover:bg-gray-700 ${busy ? 'opacity-50' : ''}`}>
      {busy ? 'Importing…' : 'Import edited MP3'}
      <input
        type="file"
        accept="audio/mpeg,.mp3"
        className="hidden"
        disabled={busy}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (!file) return;
          setBusy(true);
          api
            .importMp3(sid, field.fid, file)
            .then((updated) => {
              onUpdate(updated);
              toast.success('Imported as the new working master.');
            })
            .catch((err: unknown) => toast.error(`Import failed: ${err instanceof ApiError ? err.detail : 'network error'}`))
            .finally(() => setBusy(false));
        }}
      />
    </label>
  );
};

/** Submit/approve result — the two endpoints share `{ok, validation}`; approve
 * additionally reports what got written/promoted to staging. */
const ResultPanel = ({ result }: { result: NonNullable<ResultState> }) => {
  // NOTE: deliberately not destructured — `result.kind === 'approve'` only
  // narrows `result.data` to ApproveResponse when accessed through `result`
  // itself; a separately-destructured `data` binding would lose that link.
  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
      <div className="mb-2 flex items-center gap-3">
        <h2 className="text-sm font-semibold text-white">
          {result.kind === 'approve' ? 'Approve result' : 'Submit result'}
        </h2>
        {result.kind === 'approve' && result.data.awaiting_stage9 && (
          <span className="rounded bg-amber-600 px-2 py-0.5 text-xs font-medium text-white">Awaiting Stage 9 finalise</span>
        )}
      </div>
      <p className="text-xs text-gray-400">
        {result.kind === 'approve'
          ? result.data.ok
            ? 'Changed text written to staging Trip + TripGroup; corrected mp3 masters promoted.'
            : 'Not written — resolve validation first.'
          : result.data.ok
            ? 'Locked for admin review — no staging write yet.'
            : 'Blocked — resolve validation first.'}
        {result.kind === 'approve' && result.data.written.length > 0 && ` Fields written: ${result.data.written.join(', ')}.`}
        {result.kind === 'approve' && result.data.promoted_mp3.length > 0 && ` Audio promoted: ${result.data.promoted_mp3.join(', ')}.`}
      </p>
      {result.data.validation.length > 0 ? (
        <ul className="mt-2 space-y-1 text-xs text-amber-300">
          {result.data.validation.map((v, i) => (
            <li key={i}>
              {v.scene_index !== null ? `Scene ${v.scene_index} · ` : ''}
              {v.field_path}: {v.issue}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-xs text-custom-green">No validation issues.</p>
      )}
    </section>
  );
};

const ChangesSummaryPage = () => {
  const { sid = '' } = useParams<{ sid: string }>();
  const { user } = useAuth();
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editOnly, setEditOnly] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [approving, setApproving] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [result, setResult] = useState<ResultState>(null);
  const [sendBackOpen, setSendBackOpen] = useState(false);
  const [sendBackNote, setSendBackNote] = useState('');
  const [sendingBack, setSendingBack] = useState(false);

  const isAdmin = user?.role === 'admin';

  const load = () =>
    api
      .getSession(sid)
      .then((s) => setSession(s))
      .catch((e: unknown) => setError(e instanceof ApiError ? e.detail || e.code : 'Failed to load session'));

  useEffect(() => {
    let cancelled = false;
    api
      .getSession(sid)
      .then((s) => !cancelled && setSession(s))
      .catch((e: unknown) => !cancelled && setError(e instanceof ApiError ? e.detail || e.code : 'Failed to load session'));
    return () => {
      cancelled = true;
    };
  }, [sid]);

  const onUpdate = (f: Field) => setSession((s) => (s ? replaceField(s, f) : s));

  const all = useMemo(() => (session ? flatten(session) : []), [session]);
  const changed = useMemo(() => all.filter((ff) => ff.field.current_text !== ff.field.original_text), [all]);
  const editRequired = useMemo(() => all.filter((ff) => ff.field.flag === 'edit_required'), [all]);
  const notDone = useMemo(() => all.filter((ff) => ff.field.flag !== 'done').length, [all]);
  const allDone = all.length > 0 && notDone === 0;
  const shown = editOnly ? changed.filter((ff) => ff.field.flag === 'edit_required') : changed;
  const editable = session ? isEditableStatus(session.status) : false;

  // C3: a plain <a href> can't carry the Authorization header (→ 401). Fetch the
  // zip as a blob with the header, then trigger a download from an object URL.
  const downloadAll = () => {
    setDownloading(true);
    api
      .downloadZip(sid)
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${session?.trip_id ?? 'session'}.zip`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      })
      .catch((e: unknown) => toast.error(`Download failed: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setDownloading(false));
  };

  const submit = () => {
    setSubmitting(true);
    api
      .submit(sid)
      .then((r) => {
        setResult({ kind: 'submit', data: r });
        if (r.ok) toast.success('Submitted for review.');
        else toast.warn('Submit blocked by validation — see the list.');
        return load();
      })
      .catch((e: unknown) => toast.error(`Submit failed: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setSubmitting(false));
  };

  const approve = () => {
    setApproving(true);
    api
      .approve(sid)
      .then((r) => {
        setResult({ kind: 'approve', data: r });
        if (r.ok) toast.success('Approved — written to staging.');
        else toast.warn('Approved with validation issues — see the list.');
        return load();
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 409) {
          toast.error('No longer awaiting approval (already actioned elsewhere) — refreshing.');
          void load();
        } else {
          toast.error(`Approve failed: ${e instanceof ApiError ? e.detail : 'network error'}`);
        }
      })
      .finally(() => setApproving(false));
  };

  const submitSendBack = () => {
    if (!sendBackNote.trim()) {
      toast.warn('Add a note explaining what needs to change.');
      return;
    }
    setSendingBack(true);
    api
      .requestChanges(sid, sendBackNote.trim())
      .then(() => {
        toast.success('Sent back to the reviewer.');
        setSendBackOpen(false);
        setSendBackNote('');
        return load();
      })
      .catch((e: unknown) => toast.error(`Send back failed: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setSendingBack(false));
  };

  if (error) {
    return (
      <>
        <NavBar title="Changes summary" backTo="/" />
        <p className="mx-auto max-w-review px-4 py-8 text-red-300">{error}</p>
      </>
    );
  }
  if (!session) {
    return (
      <>
        <NavBar title="Changes summary" backTo="/" />
        <p className="mx-auto max-w-review px-4 py-8 text-gray-400">Loading…</p>
      </>
    );
  }

  return (
    <>
      <NavBar
        title="Changes summary"
        subtitle={session.trip_id}
        backTo={`/review/${session.id}`}
        backLabel="Review"
        right={
          <>
            <button
              type="button"
              disabled={downloading}
              onClick={downloadAll}
              className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-200 hover:bg-gray-700 disabled:opacity-50"
            >
              {downloading ? 'Downloading…' : 'Download all'}
            </button>

            {editable && (
              <button
                type="button"
                disabled={submitting || !allDone}
                onClick={submit}
                title={allDone ? 'Submit for review' : `${notDone} section(s) not yet marked done — finish them on the Review page`}
                className="rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
              >
                {submitting ? 'Submitting…' : allDone ? 'Submit for review' : `Submit (${notDone} not done)`}
              </button>
            )}

            {session.status === 'submitted' && isAdmin && (
              <>
                <button
                  type="button"
                  disabled={approving}
                  onClick={() => setSendBackOpen(true)}
                  className="rounded border border-amber-600 px-3 py-1.5 text-sm text-amber-400 hover:bg-gray-700 disabled:opacity-50"
                >
                  Send back
                </button>
                <button
                  type="button"
                  disabled={approving}
                  onClick={approve}
                  className="rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
                >
                  {approving ? 'Approving…' : 'Approve'}
                </button>
              </>
            )}

            {session.status === 'submitted' && !isAdmin && (
              <span className="rounded bg-blue-600 px-2 py-1 text-xs font-medium text-white">Awaiting approval</span>
            )}
            {session.status === 'approving' && (
              <span className="rounded bg-blue-500 px-2 py-1 text-xs font-medium text-white">Approving…</span>
            )}
            {session.status === 'approved' && (
              <span className="rounded bg-emerald-700 px-2 py-1 text-xs font-medium text-white">Approved</span>
            )}
          </>
        }
      />

      <main className="mx-auto max-w-review space-y-6 px-4 py-6">
        {session.status === 'changes_requested' && (
          <div className="rounded border border-amber-700 bg-amber-900/20 p-3 text-sm text-amber-200">
            <p className="font-medium">Changes requested by the admin.</p>
            {session.review_note && <p className="mt-1 whitespace-pre-wrap text-amber-100">{session.review_note}</p>}
          </div>
        )}

        {(session.status === 'submitted' || session.status === 'approving') && session.submitted_by && (
          <p className="text-xs text-gray-500">Submitted by {session.submitted_by}.</p>
        )}
        {session.status === 'approved' && session.approved_by && (
          <p className="text-xs text-gray-500">Approved by {session.approved_by}.</p>
        )}

        {result && <ResultPanel result={result} />}

        {/* Manual edit queue */}
        <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
          <h2 className="mb-2 text-sm font-semibold text-white">Manual-edit queue ({editRequired.length})</h2>
          {editRequired.length === 0 && <p className="text-xs text-gray-500">Nothing flagged edit-required.</p>}
          <ul className="space-y-3">
            {editRequired.map((ff) => (
              <li key={ff.field.fid} className="rounded border border-amber-700/50 bg-amber-900/10 p-3">
                <p className="text-xs font-medium text-amber-300">{fieldLabel(ff)}</p>
                {ff.field.comment && <p className="mt-1 text-xs text-gray-300">Note: {ff.field.comment}</p>}
                <div className="mt-2 flex flex-wrap items-center gap-3">
                  {ff.field.audio.original && (
                    <a className="text-xs text-gray-400 underline hover:text-gray-200" href={ff.field.audio.original}>
                      original
                    </a>
                  )}
                  {ff.field.audio.fallback && (
                    <a className="text-xs text-gray-400 underline hover:text-gray-200" href={ff.field.audio.fallback}>
                      standalone clip
                    </a>
                  )}
                  {ff.field.has_audio && editable && <ImportMp3 field={ff.field} sid={session.id} onUpdate={onUpdate} />}
                </div>
                {ff.field.manual_clips.length > 0 && (
                  <div className="mt-3 space-y-2 border-t border-amber-700/30 pt-2">
                    <p className="text-[11px] font-medium uppercase tracking-wide text-amber-300/80">
                      Attached takes ({ff.field.manual_clips.length})
                    </p>
                    {ff.field.manual_clips.map((c) => (
                      <div key={c.id} className="rounded border border-gray-700 bg-gray-900/40 p-2">
                        <span className="text-[11px] uppercase tracking-wide text-gray-500">
                          {c.kind} · attachment {c.id}
                        </span>
                        {c.comment && (
                          <p className="mt-0.5 text-xs text-amber-200">
                            <span className="font-medium">Note:</span> {c.comment}
                          </p>
                        )}
                        <audio controls preload="none" src={c.url} className="mt-1 h-8 w-full" />
                      </div>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>

        {/* Changed fields */}
        <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-white">Changed fields ({changed.length})</h2>
            <label className="flex items-center gap-2 text-xs text-gray-300">
              <input type="checkbox" checked={editOnly} onChange={(e) => setEditOnly(e.target.checked)} />
              edit-required only
            </label>
          </div>
          {shown.length === 0 && <p className="text-xs text-gray-500">No changes to show.</p>}
          <ul className="space-y-3">
            {shown.map((ff) => (
              <li key={ff.field.fid}>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-gray-300">{fieldLabel(ff)}</span>
                  {ff.field.flag !== 'none' && (
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] ${ff.field.flag === 'done' ? 'bg-custom-green' : 'bg-amber-600'} text-white`}
                    >
                      {ff.field.flag}
                    </span>
                  )}
                </div>
                <InlineDiff original={ff.field.original_text} current={ff.field.current_text} />
              </li>
            ))}
          </ul>
        </section>
      </main>

      <Modal
        isOpen={sendBackOpen}
        onRequestClose={() => !sendingBack && setSendBackOpen(false)}
        style={MODAL_STYLE}
        contentLabel="Send back to reviewer"
      >
        <h2 className="mb-2 text-sm font-semibold">Send back to the reviewer</h2>
        <p className="mb-3 text-xs text-gray-400">
          The reviewer will see this note and can edit the session again. It re-opens for editing (status →{' '}
          <span className="text-amber-300">changes requested</span>).
        </p>
        <textarea
          value={sendBackNote}
          onChange={(e) => setSendBackNote(e.target.value)}
          placeholder="What needs to change?"
          rows={4}
          autoFocus
          className="mb-3 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm"
        />
        <div className="flex justify-end gap-2">
          <button
            type="button"
            disabled={sendingBack}
            onClick={() => setSendBackOpen(false)}
            className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={sendingBack}
            onClick={submitSendBack}
            className="rounded border border-amber-600 px-3 py-1.5 text-sm text-amber-400 hover:bg-gray-700 disabled:opacity-50"
          >
            {sendingBack ? 'Sending…' : 'Send back'}
          </button>
        </div>
      </Modal>
    </>
  );
};

export default ChangesSummaryPage;
