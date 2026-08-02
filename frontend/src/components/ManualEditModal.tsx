import { useState } from 'react';
import Modal from 'react-modal';
import { toast } from 'react-toastify';
import { api, ApiError, type Field, type ManualClip } from '../api';

const MODAL_STYLE: Modal.Styles = {
  overlay: { backgroundColor: 'rgba(0,0,0,0.6)', zIndex: 50 },
  content: {
    inset: '50% auto auto 50%',
    transform: 'translate(-50%,-50%)',
    maxWidth: '560px',
    width: '92%',
    maxHeight: '85vh',
    overflow: 'auto',
    background: '#111827',
    border: '1px solid #374151',
    borderRadius: '0.5rem',
    padding: '1rem',
    color: 'white',
  },
};

const btn =
  'rounded border px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-40 enabled:hover:bg-gray-700';

interface Props {
  field: Field;
  sid: string;
  isOpen: boolean;
  onClose: () => void;
  onFieldUpdate: (f: Field) => void;
  /** "Save & insert" — keep the take but hand the reviewer to the waveform editor, which
   * is what actually pastes it into the working audio. Absent → that action is hidden. */
  onInsertRequested?: () => void;
}

interface RowProps {
  clip: ManualClip;
  sid: string;
  fid: number;
  busy: boolean;
  setBusy: (b: boolean) => void;
  onFieldUpdate: (f: Field) => void;
}

/** A SAVED take: re-voice its text, edit the admin note, or delete it. A take with a note
 * is an instruction to the admin; one without is the reviewer's own, kept to paste into
 * the waveform. Both are listed — adding a note later promotes it to the admin queue. */
const ClipRow = ({ clip, sid, fid, busy, setBusy, onFieldUpdate }: RowProps) => {
  const [text, setText] = useState(clip.text);
  const [note, setNote] = useState(clip.comment);
  const isGen = clip.kind === 'generated';
  const forAdmin = Boolean(clip.comment.trim());

  const run = async (fn: () => Promise<Field>, label: string) => {
    setBusy(true);
    try {
      onFieldUpdate(await fn());
    } catch (e) {
      toast.error(`${label} failed: ${e instanceof ApiError ? e.detail : 'network error'}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-1 rounded border border-gray-700 bg-gray-900/40 p-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] uppercase tracking-wide text-gray-500">
          {clip.kind} · attachment {clip.id}
        </span>
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] ${
            forAdmin ? 'bg-amber-600/80 text-white' : 'bg-sky-900/70 text-sky-300'
          }`}
          title={
            forAdmin
              ? 'Has a note → the admin sees this in the manual-edit queue'
              : 'No note → yours to drop into the audio with “Insert new” in the waveform editor'
          }
        >
          {forAdmin ? 'for the admin' : 'to insert yourself'}
        </span>
      </div>
      {isGen ? (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-base sm:text-sm"
        />
      ) : (
        <p className="text-xs italic text-gray-400">Imported file</p>
      )}
      <audio controls preload="none" src={clip.url} className="h-8 w-full" />
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        rows={2}
        placeholder="Note for the admin"
        className="w-full rounded border border-amber-700/60 bg-gray-900 px-2 py-1 text-base text-amber-200 sm:text-sm"
      />
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy || note.trim() === clip.comment.trim()}
          onClick={() => run(() => api.setClipComment(sid, fid, clip.id, note.trim()), 'Save note')}
          className={`${btn} border-gray-600 text-gray-200`}
        >
          Save note
        </button>
        {isGen && (
          <button
            type="button"
            disabled={busy}
            onClick={() => run(() => api.regenClip(sid, fid, clip.id, text), 'Regenerate')}
            className={`${btn} border-gray-600 text-gray-200`}
          >
            Re-voice
          </button>
        )}
        <button
          type="button"
          disabled={busy}
          onClick={() => run(() => api.deleteClip(sid, fid, clip.id), 'Delete')}
          className={`${btn} border-red-700 text-red-400`}
        >
          Delete
        </button>
      </div>
    </div>
  );
};

/** Per-field "Create new" workspace. Generate/Import produce an unsaved DRAFT take you can
 * audition; you then commit it one of two ways — neither replaces the working audio:
 *
 *   "Save attachment"  → note REQUIRED; flags the field edit-required so the ADMIN acts on it.
 *   "Save & insert"    → no note; the reviewer is going to paste it into the waveform
 *                        themselves, so demanding instructions for an admin who will never
 *                        see it is pure friction (and the edit-required flag would be a lie).
 *
 * Either way the take lands in `field.manual_clips`, which is what the waveform editor's
 * "Insert new" pastes from. */
const ManualEditModal = ({ field, sid, isOpen, onClose, onFieldUpdate, onInsertRequested }: Props) => {
  const [busy, setBusy] = useState(false);
  const [newText, setNewText] = useState(field.current_text);
  const [comment, setComment] = useState('');
  const [draftId, setDraftId] = useState<number | null>(null);

  const draft = draftId === null ? null : field.manual_clips.find((c) => c.id === draftId) ?? null;
  // Everything that isn't the in-progress draft is saved — with a note (for the admin) or
  // without (saved to insert here). Filtering on the note would hide the latter, which is
  // exactly the set the waveform editor offers to paste.
  const saved = field.manual_clips.filter((c) => c.id !== draftId);

  const reset = () => {
    setDraftId(null);
    setComment('');
    setNewText(field.current_text);
  };

  const generate = async () => {
    if (!newText.trim()) {
      toast.warn('Enter text for the clip.');
      return;
    }
    setBusy(true);
    try {
      if (draftId !== null) {
        // Re-voice the existing draft in place (a fresh take of the same text).
        onFieldUpdate(await api.regenClip(sid, field.fid, draftId, newText));
        toast.success('Re-voiced the draft — audition it, then Save attachment.');
      } else {
        const prev = new Set(field.manual_clips.map((c) => c.id));
        const updated = await api.createClip(sid, field.fid, newText, '');
        onFieldUpdate(updated);
        const added = updated.manual_clips.find((c) => !prev.has(c.id));
        if (added) setDraftId(added.id);
        toast.success('Draft take created — audition it, add a note, then Save attachment.');
      }
    } catch (e) {
      toast.error(`Generate failed: ${e instanceof ApiError ? e.detail : 'network error'}`);
    } finally {
      setBusy(false);
    }
  };

  const onImport = async (file?: File) => {
    if (!file) return;
    if (draftId !== null) {
      toast.warn('Save or discard the current draft before importing another take.');
      return;
    }
    setBusy(true);
    try {
      const prev = new Set(field.manual_clips.map((c) => c.id));
      const updated = await api.importClip(sid, field.fid, file, '');
      onFieldUpdate(updated);
      const added = updated.manual_clips.find((c) => !prev.has(c.id));
      if (added) setDraftId(added.id);
      toast.success('File attached as a draft — add a note, then Save attachment.');
    } catch (e) {
      toast.error(`Import failed: ${e instanceof ApiError ? e.detail : 'network error'}`);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (draftId === null) return;
    if (!comment.trim()) {
      toast.warn('Add a note for the admin describing what to do with this take.');
      return;
    }
    setBusy(true);
    try {
      onFieldUpdate(await api.setClipComment(sid, field.fid, draftId, comment.trim()));
      reset();
      toast.success('Take saved — field flagged edit-required. Create another if needed.');
    } catch (e) {
      toast.error(`Save failed: ${e instanceof ApiError ? e.detail : 'network error'}`);
    } finally {
      setBusy(false);
    }
  };

  // Commit the draft with NO note and go to the waveform editor to paste it. The note is
  // an instruction to the admin; when the reviewer does the work themselves there is no
  // admin in the loop, so requiring one (and the edit-required flag it raises) would be
  // wrong as well as slow.
  const saveForInsert = () => {
    if (draftId === null) return;
    reset();
    onInsertRequested?.();
  };

  // Drop an unsaved draft (its audio was never committed).
  const discardDraft = async () => {
    if (draftId === null) return;
    setBusy(true);
    try {
      onFieldUpdate(await api.deleteClip(sid, field.fid, draftId));
    } catch {
      /* best effort */
    } finally {
      reset();
      setBusy(false);
    }
  };

  const handleClose = async () => {
    if (busy) return;
    if (draftId !== null) {
      try {
        onFieldUpdate(await api.deleteClip(sid, field.fid, draftId));
      } catch {
        /* best effort */
      }
    }
    reset();
    onClose();
  };

  return (
    <Modal isOpen={isOpen} onRequestClose={handleClose} style={MODAL_STYLE} contentLabel="Create new">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Create new — attachments for the admin</h2>
        <button type="button" onClick={handleClose} className="text-xs text-gray-400 hover:text-gray-200">
          Close
        </button>
      </div>
      <p className="mb-3 text-xs text-gray-400">
        Generate a take (voiced verbatim at the trip’s voice) or import your own mp3, then audition it. Save it{' '}
        <span className="font-medium text-gray-200">with a note</span> to hand it to the admin (the field is flagged{' '}
        <span className="text-amber-300">edit-required</span>), or{' '}
        <span className="text-sky-300">Save &amp; insert</span> to drop it into the audio yourself in the waveform
        editor. Neither replaces the working audio on its own.
      </p>

      <div className="mb-3 space-y-2 rounded border border-gray-700 bg-gray-900/40 p-2">
        <textarea
          value={newText}
          onChange={(e) => setNewText(e.target.value)}
          rows={2}
          placeholder="Text to voice for a new take"
          className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-base sm:text-sm"
        />
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" disabled={busy} onClick={generate} className={`${btn} border-gray-600 text-gray-200`}>
            {draftId !== null ? 'Re-voice draft' : 'Generate take'}
          </button>
          <label
            className={`${btn} border-gray-600 text-gray-200 ${
              busy || draftId !== null ? 'cursor-not-allowed opacity-40' : 'cursor-pointer'
            }`}
          >
            Import mp3…
            <input
              type="file"
              accept="audio/mpeg,.mp3"
              className="hidden"
              disabled={busy || draftId !== null}
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = '';
                void onImport(f);
              }}
            />
          </label>
          {busy && <span className="text-xs text-gray-500">working…</span>}
        </div>

        {draft && (
          <div className="space-y-2 rounded border border-sky-800/60 bg-sky-950/30 p-2">
            <span className="text-[11px] uppercase tracking-wide text-sky-400">Draft — not saved yet</span>
            <audio controls preload="none" src={draft.url} className="h-8 w-full" />
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={2}
              placeholder="Note for the admin (required) — e.g. ‘use this take, the master mispronounces …’"
              className="w-full rounded border border-amber-700/60 bg-gray-900 px-2 py-1 text-base sm:text-sm"
            />
            <div className="flex flex-wrap gap-2">
              <button type="button" disabled={busy} onClick={save} className={`${btn} border-custom-green text-custom-green`}>
                Save attachment
              </button>
              {onInsertRequested && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={saveForInsert}
                  title="Keep this take and open the waveform, where you can delete the old audio and drop this in at the cursor. No note needed — you're doing the edit yourself."
                  className={`${btn} border-sky-600 text-sky-300`}
                >
                  Save &amp; insert…
                </button>
              )}
              <button type="button" disabled={busy} onClick={discardDraft} className={`${btn} border-red-700 text-red-400`}>
                Discard draft
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="space-y-2">
        {saved.length === 0 && !draft && <p className="text-xs text-gray-500">No saved attachments yet.</p>}
        {saved.map((c) => (
          <ClipRow key={c.id} clip={c} sid={sid} fid={field.fid} busy={busy} setBusy={setBusy} onFieldUpdate={onFieldUpdate} />
        ))}
      </div>
    </Modal>
  );
};

export default ManualEditModal;
