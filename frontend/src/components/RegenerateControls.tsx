import { useState } from 'react';
import Modal from 'react-modal';
import { toast } from 'react-toastify';
import { api, ApiError, type Field, type RegenerateMode } from '../api';
import ManualEditModal from './ManualEditModal';

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
  const [manualOpen, setManualOpen] = useState(false);
  const [altOpen, setAltOpen] = useState(false);
  const [altRange, setAltRange] = useState<{ start: number; end: number } | null>(null);
  const [altText, setAltText] = useState('');
  const [altWhole, setAltWhole] = useState(false); // true → voice alt text as the WHOLE field

  const afterRegen = (updated: Field) => {
    onFieldUpdate(updated);
    if (!updated.audio.candidate && updated.flag === 'edit_required') {
      toast.info('Could not splice automatically — flagged edit-required. Try whole-regenerate or send to manual edit.');
    }
  };

  const regen = async (
    mode: RegenerateMode,
    range?: { start: number; end: number },
    alt?: string,
  ) => {
    setBusy(true);
    try {
      await onBeforeRegenerate?.(); // S3: persist the latest text before the server diffs it
      const updated = await api.regenerate(sid, field.fid, mode, range, alt);
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

  // Manual backstop: highlight the spot where a leftover sliver/noise sits → trim it from
  // the working take. Operates straight on the working audio (no candidate/combine step).
  const onTrimNoise = async () => {
    const range = getSelectionRange?.() ?? null;
    if (!range || range.start === range.end) {
      toast.warn('Highlight the space where the unwanted noise/sliver is, then click.');
      return;
    }
    setBusy(true);
    try {
      await onBeforeRegenerate?.(); // align char offsets with the saved text
      const updated = await api.trimNoise(sid, field.fid, range.start, range.end);
      onFieldUpdate(updated);
      toast.success('Trimmed any sliver in the highlighted space — re-listen to confirm.');
    } catch (e: unknown) {
      toast.error(`Trim failed: ${e instanceof ApiError ? e.detail : 'network error'}`);
    } finally {
      setBusy(false);
    }
  };

  // Capture the selection NOW (before focus moves to the modal input deselects it).
  const onAltText = () => {
    const range = getSelectionRange?.() ?? null;
    if (!range || range.start === range.end) {
      toast.warn('Select the phrase to replace in the narration first.');
      return;
    }
    setAltWhole(false);
    setAltRange(range);
    // Prefill with the highlighted words so the reviewer tweaks the spelling/phonetics
    // rather than retyping the whole phrase from scratch.
    setAltText(field.current_text.slice(range.start, range.end).trim());
    setAltOpen(true);
  };

  // Whole-field alt text (question options / Q&A — no selection): voice it as the whole block.
  const onAltTextWhole = () => {
    setAltWhole(true);
    setAltRange(null);
    setAltText('');
    setAltOpen(true);
  };

  const submitAlt = () => {
    if (!altText.trim()) {
      toast.warn('Type the text for ElevenLabs to speak.');
      return;
    }
    if (altWhole) regen('whole', undefined, altText.trim());
    else if (altRange) regen('alt', altRange, altText.trim());
    setAltOpen(false);
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

  const btn =
    'rounded border px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-40 enabled:hover:bg-gray-700';

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" disabled={busy} onClick={() => regen('whole')} className={`${btn} border-gray-600 text-gray-200`}>
        Regenerate whole block
      </button>

      {wholeOnly && (
        <button
          type="button"
          disabled={busy}
          onClick={onAltTextWhole}
          title="Voice replacement/phonetic text as the whole block"
          className={`${btn} border-gray-600 text-gray-200`}
        >
          …with alt text
        </button>
      )}

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
          <button
            type="button"
            disabled={busy}
            onClick={onAltText}
            title="Select text in the narration, then supply alternate/phonetic text to speak"
            className={`${btn} border-gray-600 text-gray-200`}
          >
            …with alt text
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onTrimNoise}
            title="Backstop: highlight the space where unwanted noise/a leftover sliver is heard, then click to trim it from the working take"
            className={`${btn} border-gray-600 text-gray-200`}
          >
            Trim highlighted noise
          </button>
        </>
      )}

      {field.audio.candidate && (
        <button type="button" disabled={busy} onClick={doCombine} className={`${btn} border-custom-green text-custom-green`}>
          Combine
        </button>
      )}

      <button type="button" disabled={busy} onClick={() => setManualOpen(true)} className={`${btn} border-amber-600 text-amber-400`}>
        Manual edit
      </button>

      {busy && <span className="text-xs text-gray-500">working…</span>}

      <ManualEditModal
        field={field}
        sid={sid}
        isOpen={manualOpen}
        onClose={() => setManualOpen(false)}
        onFieldUpdate={onFieldUpdate}
      />

      <Modal
        isOpen={altOpen}
        onRequestClose={() => !busy && setAltOpen(false)}
        style={MODAL_STYLE}
        contentLabel="Regenerate highlighted with alt text"
      >
        <h2 className="mb-2 text-sm font-semibold">
          {altWhole ? 'Regenerate the whole block with alt text' : 'Replace highlighted audio with alt text'}
        </h2>
        <p className="mb-3 text-xs text-gray-400">
          ElevenLabs voices this text verbatim (spell it phonetically to fix a tricky pronunciation, e.g.
          “Christ-church”). {altWhole ? 'It replaces the entire field’s audio.' : 'It is spliced into the highlighted spot.'}{' '}
          The on-screen text is unchanged.
        </p>
        <textarea
          value={altText}
          onChange={(e) => setAltText(e.target.value)}
          placeholder={altWhole ? 'Text to speak for the whole block' : 'Text to speak in place of the highlighted words'}
          rows={2}
          autoFocus
          className="mb-3 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm"
        />
        <div className="flex justify-end gap-2">
          <button type="button" disabled={busy} onClick={() => setAltOpen(false)} className={`${btn} border-gray-600 text-gray-300`}>
            Cancel
          </button>
          <button type="button" disabled={busy} onClick={submitAlt} className={`${btn} border-custom-green text-custom-green`}>
            {altWhole ? 'Generate' : 'Generate & splice'}
          </button>
        </div>
      </Modal>
    </div>
  );
};

export default RegenerateControls;
