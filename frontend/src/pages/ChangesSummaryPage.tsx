import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { toast } from 'react-toastify';
import { api, ApiError, type Field, type Session, type SubmitResponse } from '../api';
import NavBar from '../components/NavBar';
import InlineDiff from '../components/InlineDiff';

interface FlatField {
  field: Field;
  sceneIndex: number | null;
}

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

const ChangesSummaryPage = () => {
  const { sid = '' } = useParams<{ sid: string }>();
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editOnly, setEditOnly] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [result, setResult] = useState<SubmitResponse | null>(null);

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
  const shown = editOnly ? changed.filter((ff) => ff.field.flag === 'edit_required') : changed;

  // C3: a plain <a href> can't carry the X-Review-Token header (→ 401). Fetch the
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
        setResult(r);
        if (r.ok) toast.success('Submitted to staging.');
        else toast.warn('Submit blocked by validation — see the list.');
      })
      .catch((e: unknown) => toast.error(`Submit failed: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setSubmitting(false));
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
            <button
              type="button"
              disabled={submitting}
              onClick={submit}
              className="rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {submitting ? 'Submitting…' : 'Submit'}
            </button>
          </>
        }
      />

      <main className="mx-auto max-w-review space-y-6 px-4 py-6">
        {result && (
          <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
            <div className="mb-2 flex items-center gap-3">
              <h2 className="text-sm font-semibold text-white">Submit result</h2>
              {result.awaiting_stage9 && (
                <span className="rounded bg-amber-600 px-2 py-0.5 text-xs font-medium text-white">Awaiting Stage 9 finalise</span>
              )}
            </div>
            <p className="text-xs text-gray-400">
              {result.ok ? 'Changed text written to staging Trip + TripGroup.' : 'Not written — resolve validation first.'}
              {result.written.length > 0 && ` Fields written: ${result.written.join(', ')}.`}
            </p>
            {result.validation.length > 0 ? (
              <ul className="mt-2 space-y-1 text-xs text-amber-300">
                {result.validation.map((v, i) => (
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
                  {ff.field.has_audio && <ImportMp3 field={ff.field} sid={session.id} onUpdate={onUpdate} />}
                </div>
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
    </>
  );
};

export default ChangesSummaryPage;
