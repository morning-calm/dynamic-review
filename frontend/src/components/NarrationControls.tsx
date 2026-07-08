import { useEffect, useMemo, useState } from 'react';
import Modal from 'react-modal';
import { toast } from 'react-toastify';
import { api, ApiError, type Field, type NarrationUpdate, type Session, type VoiceInfo } from '../api';

interface Props {
  session: Session;
  onUpdate: (s: Session) => void;
}

const SPEED_PRESETS = [0.7, 0.85, 1.0];

const selectCls =
  'rounded border border-gray-600 bg-gray-900 px-2 py-1 text-sm text-gray-100 disabled:opacity-50';

const MODAL_STYLE: Modal.Styles = {
  overlay: { backgroundColor: 'rgba(0,0,0,0.6)', zIndex: 50 },
  content: {
    inset: '50% auto auto 50%',
    transform: 'translate(-50%,-50%)',
    maxWidth: '460px',
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

interface Pending {
  body: NarrationUpdate; // without reset_regenerated — the modal supplies that
  label: string;
}

/** Per-trip narration settings: which approved voice narrates the trip, plus speed /
 * model overrides. Changing a setting that affects regeneration asks whether to keep
 * or discard any audio already regenerated under the old settings (the master audio is
 * never touched, and text edits are always kept). */
const NarrationControls = ({ session, onUpdate }: Props) => {
  const [voices, setVoices] = useState<VoiceInfo[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  // Narration is a stricter gate than the general editable-status set: the
  // backend only allows /narration while status is exactly 'in_review' (not
  // 'changes_requested' too) — a voice/speed/model change is disruptive enough
  // (can reset every field's regenerated audio) that it's restricted to the
  // first review pass. See sessions.py:set_narration.
  const disabled = busy || session.status !== 'in_review';

  useEffect(() => {
    let cancelled = false;
    api
      .listVoices()
      .then((r) => {
        if (cancelled) return;
        setVoices(r.voices);
        setModels(r.models);
      })
      .catch(() => {
        /* picker just stays empty; current value is still shown */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // How many fields carry audio regenerated in-review (pending candidate or a take
  // beyond the v0 master)? Those are what a discard would reset.
  const regenCount = useMemo(() => {
    const all: Field[] = [...session.trip_fields, ...session.scenes.flatMap((s) => s.fields)];
    return all.filter((f) => f.audio.candidate || f.versions.length > 1).length;
  }, [session]);

  const apply = async (body: NarrationUpdate, label: string) => {
    setBusy(true);
    try {
      const s = await api.setNarration(session.id, body);
      onUpdate(s);
      toast.success(`Narration: ${label}`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.detail || e.code : 'Update failed');
    } finally {
      setBusy(false);
    }
  };

  // Changes that affect regeneration go through the keep/discard popup when there is
  // regenerated audio; otherwise they apply straight away.
  const requestChange = (body: NarrationUpdate, label: string) => {
    if (regenCount > 0) setPending({ body, label });
    else void apply({ ...body, reset_regenerated: false }, label);
  };

  const resolvePending = (reset: boolean) => {
    if (!pending) return;
    const { body, label } = pending;
    setPending(null);
    void apply({ ...body, reset_regenerated: reset }, label);
  };

  const onVoice = (name: string) => {
    if (name === session.voice) return;
    requestChange({ voice: name }, voices.find((v) => v.name === name)?.display ?? name);
  };

  const onSpeed = (val: string) => {
    if (val === 'auto') requestChange({ clear_speed: true }, 'speed = auto');
    else requestChange({ speed: Number(val) }, `speed = ${val}`);
  };

  const onModel = (val: string) => {
    if (val === 'auto') requestChange({ clear_model: true }, 'model = auto');
    else requestChange({ model: val }, val);
  };

  const speedVal = session.speed_override == null ? 'auto' : String(session.speed_override);
  const modelVal = session.model_override ?? 'auto';
  const speedOpts = Array.from(
    new Set([...SPEED_PRESETS, ...(session.speed_override != null ? [session.speed_override] : [])]),
  ).sort();

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-gray-700 bg-gray-800/60 p-3 text-sm">
      <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">Narration</span>

      <label className="flex items-center gap-1.5 text-gray-300">
        Voice
        <select className={selectCls} value={session.voice} disabled={disabled} onChange={(e) => onVoice(e.target.value)}>
          {voices.length === 0 && <option value={session.voice}>{session.voice_display}</option>}
          {voices.map((v) => (
            <option key={v.name} value={v.name}>
              {v.display} · {v.language}/{v.country} · {v.gender === 'female' ? '♀' : '♂'}
            </option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-1.5 text-gray-300">
        Speed
        <select className={selectCls} value={speedVal} disabled={disabled} onChange={(e) => onSpeed(e.target.value)}>
          <option value="auto">auto ({session.speed})</option>
          {speedOpts.map((s) => (
            <option key={s} value={String(s)}>
              {s}
            </option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-1.5 text-gray-300">
        Model
        <select className={selectCls} value={modelVal} disabled={disabled} onChange={(e) => onModel(e.target.value)}>
          <option value="auto">auto ({session.model})</option>
          {models.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </label>

      {busy && <span className="text-xs text-gray-500">saving…</span>}

      <Modal
        isOpen={pending !== null}
        onRequestClose={() => !busy && setPending(null)}
        style={MODAL_STYLE}
        contentLabel="Change narration"
      >
        <h2 className="mb-2 text-sm font-semibold">Change narration to {pending?.label}</h2>
        <p className="mb-4 text-xs text-gray-400">
          {regenCount} field{regenCount === 1 ? '' : 's'} {regenCount === 1 ? 'has' : 'have'} audio you regenerated
          under the current settings. Keep that audio, or discard it back to the master so you can re-generate it under
          the new settings? Your text edits are kept either way.
        </p>
        <div className="flex flex-wrap justify-end gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => setPending(null)}
            className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => resolvePending(false)}
            className="rounded border border-custom-green px-3 py-1.5 text-sm text-custom-green hover:bg-gray-700"
          >
            Keep generated audio
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => resolvePending(true)}
            className="rounded border border-amber-600 px-3 py-1.5 text-sm text-amber-400 hover:bg-gray-700"
          >
            Discard generated audio
          </button>
        </div>
      </Modal>
    </div>
  );
};

export default NarrationControls;
