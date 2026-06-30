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
}

interface RowProps {
  clip: ManualClip;
  sid: string;
  fid: number;
  busy: boolean;
  setBusy: (b: boolean) => void;
  onFieldUpdate: (f: Field) => void;
}

const ClipRow = ({ clip, sid, fid, busy, setBusy, onFieldUpdate }: RowProps) => {
  const [text, setText] = useState(clip.text);
  const isGen = clip.kind === 'generated';

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
      <span className="text-[11px] uppercase tracking-wide text-gray-500">
        {clip.kind} · clip {clip.id}
      </span>
      {isGen ? (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm"
        />
      ) : (
        <p className="text-xs italic text-gray-400">Imported file</p>
      )}
      <audio controls preload="none" src={clip.url} className="h-8 w-full" />
      <div className="flex flex-wrap gap-2">
        {isGen && (
          <button
            type="button"
            disabled={busy}
            onClick={() => run(() => api.regenClip(sid, fid, clip.id, text), 'Regenerate')}
            className={`${btn} border-gray-600 text-gray-200`}
          >
            Regenerate
          </button>
        )}
        <button
          type="button"
          disabled={busy}
          onClick={() => run(() => api.useClip(sid, fid, clip.id), 'Use')}
          className={`${btn} border-custom-green text-custom-green`}
        >
          Use as working take
        </button>
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

/** Per-field manual-edit workspace: generate/import multiple take clips, audition,
 * regenerate or delete them, then promote one to the working take. */
const ManualEditModal = ({ field, sid, isOpen, onClose, onFieldUpdate }: Props) => {
  const [busy, setBusy] = useState(false);
  const [newText, setNewText] = useState(field.current_text);

  const generate = async () => {
    if (!newText.trim()) {
      toast.warn('Enter text for the clip.');
      return;
    }
    setBusy(true);
    try {
      onFieldUpdate(await api.createClip(sid, field.fid, newText));
      toast.success('Clip generated.');
    } catch (e) {
      toast.error(`Generate failed: ${e instanceof ApiError ? e.detail : 'network error'}`);
    } finally {
      setBusy(false);
    }
  };

  const onImport = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    try {
      onFieldUpdate(await api.importClip(sid, field.fid, file));
      toast.success('Clip imported.');
    } catch (e) {
      toast.error(`Import failed: ${e instanceof ApiError ? e.detail : 'network error'}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal isOpen={isOpen} onRequestClose={() => !busy && onClose()} style={MODAL_STYLE} contentLabel="Manual edit">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Manual edit — clips</h2>
        <button type="button" onClick={onClose} className="text-xs text-gray-400 hover:text-gray-200">
          Close
        </button>
      </div>
      <p className="mb-3 text-xs text-gray-400">
        Generate take clips (voiced verbatim at the trip’s voice), audition them, then “Use as working take”
        for the one you want — or import your own mp3. Add as many as you need.
      </p>

      <div className="mb-3 space-y-2 rounded border border-gray-700 bg-gray-900/40 p-2">
        <textarea
          value={newText}
          onChange={(e) => setNewText(e.target.value)}
          rows={2}
          placeholder="Text to voice for a new clip"
          className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm"
        />
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" disabled={busy} onClick={generate} className={`${btn} border-custom-green text-custom-green`}>
            Generate clip
          </button>
          <label className={`${btn} cursor-pointer border-gray-600 text-gray-200`}>
            Import mp3…
            <input
              type="file"
              accept="audio/mpeg,.mp3"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = '';
                void onImport(f);
              }}
            />
          </label>
          {busy && <span className="text-xs text-gray-500">working…</span>}
        </div>
      </div>

      <div className="space-y-2">
        {field.manual_clips.length === 0 && <p className="text-xs text-gray-500">No clips yet.</p>}
        {field.manual_clips.map((c) => (
          <ClipRow key={c.id} clip={c} sid={sid} fid={field.fid} busy={busy} setBusy={setBusy} onFieldUpdate={onFieldUpdate} />
        ))}
      </div>
    </Modal>
  );
};

export default ManualEditModal;
