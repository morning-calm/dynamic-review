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
  /** True when a highlightable narration surface exists for the selection-based ops
   * (highlight / alt-in-place / trim-noise / pause tools). All languages now qualify:
   * English + JP highlight in the narration textarea (JP: the kana line), `_ZH` in the
   * Simplified (Hans) field of the 4-script block via `getSelectionRange`. */
  hasSelection?: boolean;
  /** The text `getSelectionRange` offsets index into, for the alt-modal prefill. Defaults
   * to `field.current_text`; `_ZH` passes the Hans script (the voiced text). */
  selectionSourceText?: string;
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
  hasSelection = true,
  selectionSourceText,
  onBeforeRegenerate,
}: RegenerateControlsProps) => {
  const [busy, setBusy] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [bugOpen, setBugOpen] = useState(false);
  const [bugText, setBugText] = useState('');
  const [altOpen, setAltOpen] = useState(false);
  const [altRange, setAltRange] = useState<{ start: number; end: number } | null>(null);
  const [altText, setAltText] = useState('');
  const [altWhole, setAltWhole] = useState(false); // true → voice alt text as the WHOLE field
  // The exact params of the last regenerate, so a candidate can be re-rolled identically
  // (TTS is non-deterministic → a fresh take) if the first one has an issue.
  const [lastRegen, setLastRegen] = useState<
    { mode: RegenerateMode; range?: { start: number; end: number }; alt?: string } | null
  >(null);

  const afterRegen = (updated: Field) => {
    onFieldUpdate(updated);
    if (!updated.audio.candidate && updated.flag === 'edit_required') {
      toast.info('Could not splice automatically — flagged edit-required. Try whole-regenerate or send to Create new.');
    } else if (updated.cjk_fallback) {
      // Surgical CJK splice bailed → the whole narration was regenerated, not just the edit.
      toast.info('Couldn’t splice just the edit cleanly — regenerated the whole narration. Re-listen to the full clip.');
    }
  };

  const regen = async (
    mode: RegenerateMode,
    range?: { start: number; end: number },
    alt?: string,
  ) => {
    setBusy(true);
    setLastRegen({ mode, range, alt });
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

  // Re-roll the current candidate with the IDENTICAL request (same span/alt text).
  const redoCandidate = () => {
    if (!lastRegen) return;
    regen(lastRegen.mode, lastRegen.range, lastRegen.alt);
  };

  // Undo / redo through the working take's audio version history.
  const stepHistory = (dir: 'undo' | 'redo') => {
    setBusy(true);
    (dir === 'undo' ? api.undoAudio(sid, field.fid) : api.redoAudio(sid, field.fid))
      .then((updated) => onFieldUpdate(updated))
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 409) toast.info(e.detail);
        else toast.error(`${dir} failed: ${e instanceof ApiError ? e.detail : 'network error'}`);
      })
      .finally(() => setBusy(false));
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
    // rather than retyping the whole phrase from scratch. The offsets index into the
    // surface the reviewer highlighted in (ZH: the Hans field, not current_text).
    setAltText((selectionSourceText ?? field.current_text).slice(range.start, range.end).trim());
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

  // Insert a 1s pause at the TEXT caret (normally after a full stop). The caret char
  // offset is mapped to an audio time on the backend via the clip's word timing.
  const onInsertSilence = async () => {
    const range = getSelectionRange?.() ?? null;
    if (!range) {
      toast.warn('Click in the narration where the pause should go (usually after a full stop), then click.');
      return;
    }
    setBusy(true);
    try {
      await onBeforeRegenerate?.(); // align the caret offset with the saved text
      const updated = await api.insertSilence(sid, field.fid, range.start, 1);
      onFieldUpdate(updated);
      toast.success('Extended the pause by 1s at the cursor — re-listen to confirm.');
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 409) toast.warn(e.detail); // no pause to extend
      else toast.error(`Insert failed: ${e instanceof ApiError ? e.detail : 'network error'}`);
    } finally {
      setBusy(false);
    }
  };

  // The inverse of insert: shorten an overlong pause at the caret by up to 1s. The
  // backend removes from the middle of the genuine silence run and always keeps a
  // minimum natural pause — it refuses (409) rather than touch voiced audio.
  const onRemoveSilence = async () => {
    const range = getSelectionRange?.() ?? null;
    if (!range) {
      toast.warn('Click in the narration where the too-long pause is (usually after a full stop), then click.');
      return;
    }
    setBusy(true);
    try {
      await onBeforeRegenerate?.(); // align the caret offset with the saved text
      const updated = await api.removeSilence(sid, field.fid, range.start, 1);
      onFieldUpdate(updated);
      toast.success('Shortened the pause at the cursor — re-listen to confirm.');
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 409) toast.warn(e.detail); // no pause / nothing to spare
      else toast.error(`Remove failed: ${e instanceof ApiError ? e.detail : 'network error'}`);
    } finally {
      setBusy(false);
    }
  };

  // Normalize the trailing pause: beginner trips (A1-2/N5/HSK1-2) keep ~3s of end
  // silence, every other level has excess end-silence removed. Level is decided on the
  // backend from the trip id; the working URL hash tells us whether anything changed.
  const onTrimSilence = () => {
    setBusy(true);
    const before = field.audio.working;
    api
      .trimSilence(sid, field.fid)
      .then((updated) => {
        onFieldUpdate(updated);
        if (updated.audio.working === before) toast.info('End silence already correct — nothing to trim.');
        else toast.success('Adjusted the trailing silence — re-listen to confirm.');
      })
      .catch((e: unknown) => toast.error(`Trim failed: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setBusy(false));
  };

  // Nudge the trailing trim on the candidate before combining (drop a TTS breath / the
  // start of the next sound). +50 ms trims more off the end; −50 ms restores.
  const onTrimCandidate = (deltaMs: number) => {
    setBusy(true);
    api
      .trimCandidate(sid, field.fid, deltaMs)
      .then((updated) => onFieldUpdate(updated))
      .catch((e: unknown) => toast.error(`Trim failed: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setBusy(false));
  };

  // Report a problem on this field — free text in any language; the backend snapshots the
  // current text + working/candidate audio so we see exactly what the reviewer saw.
  const submitBug = async () => {
    if (!bugText.trim()) {
      toast.warn('Describe the problem first.');
      return;
    }
    setBusy(true);
    try {
      await onBeforeRegenerate?.(); // flush the latest text so it's in the snapshot
      await api.createBugReport(sid, field.fid, bugText.trim());
      setBugOpen(false);
      setBugText('');
      toast.success('Problem reported — thanks. Track it under “Bug reports”.');
    } catch (e: unknown) {
      toast.error(`Report failed: ${e instanceof ApiError ? e.detail : 'network error'}`);
    } finally {
      setBusy(false);
    }
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

  // Saved 'Create new' attachments (a note is what commits a take) → highlight the button.
  const savedClips = field.manual_clips.filter((c) => c.comment.trim()).length;

  const trimSilenceBtn = (
    <button
      type="button"
      disabled={busy}
      onClick={onTrimSilence}
      title="Trim the silence at the end of this clip (beginner trips keep a 3s tail; other levels remove excess)"
      className={`${btn} border-gray-600 text-gray-200`}
    >
      Trim end silence
    </button>
  );

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        disabled={busy || !field.can_undo}
        onClick={() => stepHistory('undo')}
        title="Undo the last audio change (step back through this clip's takes)"
        className={`${btn} border-gray-600 text-gray-200`}
      >
        ↶ Undo
      </button>
      <button
        type="button"
        disabled={busy || !field.can_redo}
        onClick={() => stepHistory('redo')}
        title="Redo (step forward through this clip's takes)"
        className={`${btn} border-gray-600 text-gray-200`}
      >
        ↷ Redo
      </button>

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

      {wholeOnly && trimSilenceBtn}

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
          {/* Selection-based ops read getSelectionRange from the language's narration
              surface: EN/JP the SceneDesc textarea (JP: the kana line), _ZH the Simplified
              (Hans) field of the 4-script block. */}
          {hasSelection && (
            <>
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
              <button
                type="button"
                disabled={busy}
                onClick={onInsertSilence}
                title="Click in the narration where a pause should go (usually after a full stop), then click to insert a 1s silence there"
                className={`${btn} border-gray-600 text-gray-200`}
              >
                Insert 1s pause at cursor
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={onRemoveSilence}
                title="Click in the narration where a pause is too long, then click to remove up to 1s of it (a natural pause is always kept — it never cuts speech)"
                className={`${btn} border-gray-600 text-gray-200`}
              >
                Remove 1s pause at cursor
              </button>
            </>
          )}
          {trimSilenceBtn}
        </>
      )}

      {field.audio.candidate && (
        <>
          <button type="button" disabled={busy} onClick={doCombine} className={`${btn} border-custom-green text-custom-green`}>
            Combine
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onTrimCandidate(50)}
            title="Trim 50 ms more off the END of the candidate (drop a trailing breath or the start of the next sound) before combining"
            className={`${btn} border-gray-600 text-gray-200`}
          >
            Trim end −
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onTrimCandidate(-50)}
            title="Restore 50 ms of the candidate's end (undo a trim-too-far)"
            className={`${btn} border-gray-600 text-gray-200`}
          >
            Trim end +
          </button>
          <button
            type="button"
            disabled={busy || !lastRegen}
            onClick={redoCandidate}
            title="Re-roll this candidate with the exact same request (a fresh take, in case the first has an issue)"
            className={`${btn} border-gray-600 text-gray-200`}
          >
            Redo candidate
          </button>
        </>
      )}

      <button
        type="button"
        disabled={busy}
        onClick={() => setManualOpen(true)}
        title={
          savedClips > 0
            ? `${savedClips} take${savedClips === 1 ? '' : 's'} attached for the admin — open to review`
            : 'Create a new take as an attachment with instructions for the admin (does not replace the working audio)'
        }
        className={`${btn} ${
          savedClips > 0 ? 'border-amber-500 bg-amber-500/10 text-amber-300' : 'border-gray-600 text-gray-200'
        }`}
      >
        Create new{savedClips > 0 ? ` (${savedClips})` : ''}
      </button>

      <button
        type="button"
        disabled={busy}
        onClick={() => setBugOpen(true)}
        title="Report a problem with this part (write in any language). The current text + audio are saved so we can see exactly what you saw."
        className={`${btn} border-rose-500/70 text-rose-300`}
      >
        Report a problem
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

      <Modal
        isOpen={bugOpen}
        onRequestClose={() => !busy && setBugOpen(false)}
        style={MODAL_STYLE}
        contentLabel="Report a problem"
      >
        <h2 className="mb-2 text-sm font-semibold">Report a problem</h2>
        <p className="mb-3 text-xs text-gray-400">
          Describe what’s wrong with this part — you can write in your own language. The current
          text and audio are attached automatically so we can see exactly what you saw. You’ll get
          a reply under “Bug reports”.
        </p>
        <textarea
          value={bugText}
          onChange={(e) => setBugText(e.target.value)}
          placeholder="What’s wrong? (any language)"
          rows={4}
          autoFocus
          className="mb-3 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm"
        />
        <div className="flex justify-end gap-2">
          <button type="button" disabled={busy} onClick={() => setBugOpen(false)} className={`${btn} border-gray-600 text-gray-300`}>
            Cancel
          </button>
          <button type="button" disabled={busy} onClick={submitBug} className={`${btn} border-rose-400 text-rose-300`}>
            Send report
          </button>
        </div>
      </Modal>
    </div>
  );
};

export default RegenerateControls;
