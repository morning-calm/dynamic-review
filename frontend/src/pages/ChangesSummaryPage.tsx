import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import Modal from 'react-modal';
import { toast } from 'react-toastify';
import {
  api,
  ApiError,
  isEditableStatus,
  type ApproveResponse,
  type AutoReviewReport,
  type Field,
  type LocalizationBlock,
  type LocalizationScripts,
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
    maxHeight: '85vh',
    overflow: 'auto',
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

// _ZH edits live in the localization block (cur vs orig per script), NOT current_text, so
// the change list must diff each of the 4 scripts.
const ZH_SCRIPTS: [keyof LocalizationScripts, string][] = [
  ['Hant', 'Traditional'],
  ['Hans', 'Simplified'],
  ['zhuyin', 'Zhuyin'],
  ['en', 'English'],
];
const zhChangedScripts = (b: LocalizationBlock): [keyof LocalizationScripts, string][] =>
  ZH_SCRIPTS.filter(([s]) => (b.cur[s] ?? '') !== (b.orig[s] ?? ''));
const fieldChanged = (f: Field): boolean =>
  f.localization ? zhChangedScripts(f.localization).length > 0 : f.current_text !== f.original_text;

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
      {result.kind === 'approve' && (result.data.zh_warnings?.length ?? 0) > 0 && (
        <ul className="mt-2 space-y-1 text-xs text-amber-300">
          {result.data.zh_warnings!.map((w, i) => (
            <li key={i}>⚠ {w}</li>
          ))}
        </ul>
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
  const [autoReview, setAutoReview] = useState<AutoReviewReport | null>(null);
  // Report-field keys whose suggested fix has been applied this visit (disables the button).
  const [appliedFixes, setAppliedFixes] = useState<Set<string>>(new Set());
  const [applyingFix, setApplyingFix] = useState<string | null>(null);

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
    // Gate-2 auto-review report (best-effort; absent until the runner has seen the submit)
    api
      .getAutoReview(sid)
      .then((r) => !cancelled && setAutoReview(r.report))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sid]);

  const onUpdate = (f: Field) => setSession((s) => (s ? replaceField(s, f) : s));

  const all = useMemo(() => (session ? flatten(session) : []), [session]);
  const changed = useMemo(() => all.filter((ff) => fieldChanged(ff.field)), [all]);
  const editRequired = useMemo(() => all.filter((ff) => ff.field.flag === 'edit_required'), [all]);
  const notDone = useMemo(() => all.filter((ff) => ff.field.flag !== 'done').length, [all]);
  const allDone = all.length > 0 && notDone === 0;
  const shown = editOnly ? changed.filter((ff) => ff.field.flag === 'edit_required') : changed;
  const editable = session ? isEditableStatus(session.status) : false;
  const isZh = useMemo(() => all.some((ff) => ff.field.localization), [all]);

  const applyFix = (f: { scene: number | null; field: string; option: number | null }) => {
    if (f.scene == null) return;
    const key = `${f.scene}·${f.field}·${f.option ?? ''}`;
    setApplyingFix(key);
    api
      .applySuggestedFix(sid, { scene: f.scene, field: f.field, option: f.option })
      .then((res) => {
        onUpdate(res.field);
        setAppliedFixes((prev) => new Set(prev).add(key));
        const skipped = res.skipped.length ? ` (skipped ${res.skipped.map((s) => s.script).join(', ')})` : '';
        toast.success(`Applied ${res.applied.join(', ')}${skipped} — listen & confirm`);
      })
      .catch((e: unknown) => toast.error(e instanceof ApiError ? e.detail || e.code : 'Failed to apply fix'))
      .finally(() => setApplyingFix(null));
  };

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
        {session.preferred_version && (
          <p className="text-xs text-gray-500">
            Preferred audio version:{' '}
            <span className="font-medium uppercase text-gray-300">{session.preferred_version}</span>
          </p>
        )}

        {result && <ResultPanel result={result} />}

        {/* Gate-2 auto-review report (shadow mode: informational, approve stays manual) */}
        {autoReview && (
          <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
            <h2 className="mb-1 flex flex-wrap items-center gap-2 text-sm font-semibold text-white">
              Auto-review
              {autoReview.status === 'error' ? (
                <span className="rounded bg-red-700 px-2 py-0.5 text-xs font-medium">failed — review manually</span>
              ) : (
                <span
                  className={`rounded px-2 py-0.5 text-xs font-medium text-white ${
                    autoReview.flag > 0 ? 'bg-red-700' : autoReview.warn > 0 ? 'bg-amber-600' : 'bg-emerald-700'
                  }`}
                >
                  {autoReview.ok} ok · {autoReview.warn} warning · {autoReview.flag} needs human
                </span>
              )}
              <span className="text-xs font-normal text-gray-500">
                {autoReview.model} · {new Date(autoReview.created_at * 1000).toLocaleString()}
              </span>
            </h2>
            {autoReview.summary && <p className="mb-2 text-xs text-gray-300">{autoReview.summary}</p>}
            <ul className="space-y-2">
              {autoReview.fields
                .filter((f) => f.verdict !== 'ok')
                .map((f, i) => (
                  <li
                    key={i}
                    className={`rounded border p-2 text-xs ${
                      f.verdict === 'needs_human'
                        ? 'border-red-700/60 bg-red-900/10 text-red-200'
                        : 'border-amber-700/50 bg-amber-900/10 text-amber-200'
                    }`}
                  >
                    <p className="font-medium">
                      {f.verdict === 'needs_human' ? '⛔' : '⚠'} Scene {f.scene ?? '—'} · {f.field}
                      {f.option != null ? `[${f.option}]` : ''}
                    </p>
                    {f.reasons.map((r, j) => (
                      <p key={j} className="mt-0.5 text-gray-300">
                        {r}
                      </p>
                    ))}
                    {f.suggested_fix && (
                      <div className="mt-1 rounded bg-gray-900/60 p-2 text-gray-300">
                        <p className="mb-0.5 font-medium text-gray-400">
                          Suggested fix{' '}
                          {f.suggested_fix_verified === true
                            ? '(machine-verified)'
                            : f.suggested_fix_verified === false
                              ? '(FAILED verification — do not use as-is)'
                              : ''}
                        </p>
                        {Object.entries(f.suggested_fix).map(([k, v]) => (
                          <p key={k} className="break-words">
                            <span className="text-gray-500">{k}:</span> {v}
                          </p>
                        ))}
                        {isZh && editable && f.suggested_fix_verified === true && f.scene != null && (() => {
                          const key = `${f.scene}·${f.field}·${f.option ?? ''}`;
                          const done = appliedFixes.has(key);
                          return (
                            <button
                              type="button"
                              disabled={done || applyingFix === key}
                              onClick={() => applyFix(f)}
                              className={`mt-2 rounded border px-2 py-1 text-xs font-medium ${
                                done
                                  ? 'cursor-default border-emerald-700/60 text-emerald-400'
                                  : 'border-emerald-600 text-emerald-300 hover:bg-emerald-900/30'
                              } ${applyingFix === key ? 'opacity-50' : ''}`}
                            >
                              {done ? '✓ Applied — listen & confirm' : applyingFix === key ? 'Applying…' : 'Apply fix'}
                            </button>
                          );
                        })()}
                      </div>
                    )}
                  </li>
                ))}
            </ul>
            {autoReview.status === 'ok' && autoReview.fields.every((f) => f.verdict === 'ok') && (
              <p className="text-xs text-emerald-400">All changed fields passed — nothing flagged.</p>
            )}
          </section>
        )}

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
                {ff.field.localization ? (
                  <div className="mt-1 space-y-1.5">
                    {zhChangedScripts(ff.field.localization).map(([s, label]) => (
                      <div key={s}>
                        <span className="text-[10px] uppercase tracking-wide text-gray-500">{label}</span>
                        <InlineDiff
                          original={ff.field.localization!.orig[s] ?? ''}
                          current={ff.field.localization!.cur[s] ?? ''}
                        />
                      </div>
                    ))}
                  </div>
                ) : (
                  <InlineDiff original={ff.field.original_text} current={ff.field.current_text} />
                )}
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
          className="mb-3 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-base sm:text-sm"
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
