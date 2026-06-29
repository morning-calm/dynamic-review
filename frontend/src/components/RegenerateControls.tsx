import { useState } from 'react';
import Modal from 'react-modal';
import { toast } from 'react-toastify';
import { api, ApiError, type Field, type FallbackExtent, type RegenerateMode } from '../api';

interface RegenerateControlsProps {
  field: Field;
  sid: string;
  onFieldUpdate: (f: Field) => void;
  /** SceneDesc text edited since last save → enables "Generate from edit". */
  hasTextChange: boolean;
  /** Reads the textarea selection for highlight mode. Absent for Q&A (whole only). */
  getSelectionRange?: () => { start: number; end: number } | null;
  /** Q&A fields are short → whole-regenerate only. */
  wholeOnly?: boolean;
  /** Flushes a pending text save before regenerating so the server diffs the saved text (S3). */
  onBeforeRegenerate?: () => Promise<void> | void;
}

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

const RegenerateControls = ({
  field,
  sid,
  onFieldUpdate,
  hasTextChange,
  getSelectionRange,
  wholeOnly = false,
  onBeforeRegenerate,
}: RegenerateControlsProps) => {
  const [busy, setBusy] = useState(false);
  const [fallbackOpen, setFallbackOpen] = useState(false);
  const [extent, setExtent] = useState<FallbackExtent>('sentence');
  const [fallbackText, setFallbackText] = useState('');
  const [description, setDescription] = useState('');

  const afterRegen = (updated: Field) => {
    onFieldUpdate(updated);
    if (!updated.audio.candidate && updated.flag === 'edit_required') {
      toast.info('Could not splice automatically — flagged edit-required. Try whole-regenerate or send to manual edit.');
    }
  };

  const regen = async (mode: RegenerateMode, range?: { start: number; end: number }) => {
    setBusy(true);
    try {
      await onBeforeRegenerate?.(); // S3: persist the latest text before the server diffs it
      const updated = await api.regenerate(sid, field.fid, mode, range);
      afterRegen(updated);
    } catch (e: unknown) {
      toast.error(`Regenerate failed: ${e instanceof ApiError ? e.detail : 'network error'}`);
    } finally {
      setBusy(false);
    }
  };

  const onHighlight = () => {
    const range = getSelectionRange?.() ?? null;
    if (!range || range.start === range.end) {
      toast.warn('Select the phrase to regenerate in the narration first.');
      return;
    }
    regen('highlight', range);
  };

  const doCombine = () => {
    setBusy(true);
    api
      .combine(sid, field.fid)
      .then((updated) => {
        onFieldUpdate(updated);
        toast.success('Combined into the working take.');
      })
      .catch((e: unknown) => toast.error(`Combine failed: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setBusy(false));
  };

  const submitFallback = () => {
    if (!description.trim()) {
      toast.warn('Add an instruction for the admin.');
      return;
    }
    setBusy(true);
    api
      .fallback(sid, field.fid, extent, description, extent === 'custom' ? fallbackText : undefined)
      .then((updated) => {
        onFieldUpdate(updated);
        setFallbackOpen(false);
        setDescription('');
        setFallbackText('');
        toast.success('Standalone clip generated and queued for manual edit.');
      })
      .catch((e: unknown) => toast.error(`Fallback failed: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setBusy(false));
  };

  const btn =
    'rounded border px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-40 enabled:hover:bg-gray-700';

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" disabled={busy} onClick={() => regen('whole')} className={`${btn} border-gray-600 text-gray-200`}>
        Regenerate whole block
      </button>

      {!wholeOnly && (
        <>
          <button
            type="button"
            disabled={busy || !hasTextChange}
            onClick={() => regen('segment')}
            title={hasTextChange ? 'Regenerate just the edited span' : 'Edit the text first'}
            className={`${btn} border-gray-600 text-gray-200`}
          >
            Generate from edit
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onHighlight}
            title="Select text in the narration, then click"
            className={`${btn} border-gray-600 text-gray-200`}
          >
            Regenerate highlighted
          </button>
        </>
      )}

      {field.audio.candidate && (
        <button type="button" disabled={busy} onClick={doCombine} className={`${btn} border-custom-green text-custom-green`}>
          Combine
        </button>
      )}

      <button type="button" disabled={busy} onClick={() => setFallbackOpen(true)} className={`${btn} border-amber-600 text-amber-400`}>
        Send to manual edit
      </button>

      {busy && <span className="text-xs text-gray-500">working…</span>}

      <Modal
        isOpen={fallbackOpen}
        onRequestClose={() => !busy && setFallbackOpen(false)}
        style={MODAL_STYLE}
        contentLabel="Send to manual edit"
      >
        <h2 className="mb-2 text-sm font-semibold">Send to manual edit</h2>
        <p className="mb-3 text-xs text-gray-400">
          Generates a standalone ElevenLabs clip for the admin to splice by hand, plus your instruction.
        </p>

        <label className="mb-1 block text-xs text-gray-300">Extent</label>
        <select
          value={extent}
          onChange={(e) => setExtent(e.target.value as FallbackExtent)}
          className="mb-3 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm"
        >
          <option value="sentence">Sentence</option>
          <option value="scene">Whole scene</option>
          <option value="custom">Custom text…</option>
        </select>

        {extent === 'custom' && (
          <textarea
            value={fallbackText}
            onChange={(e) => setFallbackText(e.target.value)}
            placeholder="Exact text to voice for the clip"
            rows={3}
            className="mb-3 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm"
          />
        )}

        <label className="mb-1 block text-xs text-gray-300">Instruction for the admin</label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="e.g. replace the second sentence; match pace of the original"
          rows={3}
          className="mb-3 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm"
        />

        <div className="flex justify-end gap-2">
          <button type="button" disabled={busy} onClick={() => setFallbackOpen(false)} className={`${btn} border-gray-600 text-gray-300`}>
            Cancel
          </button>
          <button type="button" disabled={busy} onClick={submitFallback} className={`${btn} border-amber-600 text-amber-400`}>
            Generate clip
          </button>
        </div>
      </Modal>
    </div>
  );
};

export default RegenerateControls;
