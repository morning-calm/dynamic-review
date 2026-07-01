import { useState } from 'react';
import { toast } from 'react-toastify';
import { api, ApiError, type PreferredVersion, type Session } from '../api';

interface Props {
  session: Session;
  onUpdate: (s: Session) => void;
  /** Session is locked (submitted/approving/approved) — the pick can't change. */
  readOnly?: boolean;
}

/**
 * Per-trip V2/V3 pick for the temporary Mandarin A/B audition (review-app-
 * chinese-review.md Part 3) — one ElevenLabs version per voice, chosen after
 * auditioning the side-by-side players on each field. Purely a decision
 * record (`POST /api/sessions/{sid}/version`); it doesn't touch any audio.
 * Replaces NarrationControls in the `_ZH` review header — there's no live
 * regeneration to react to a voice/speed/model change here.
 */
const PreferredVersionControl = ({ session, onUpdate, readOnly = false }: Props) => {
  const [busy, setBusy] = useState(false);

  const pick = (version: PreferredVersion) => {
    if (busy || readOnly || session.preferred_version === version) return;
    setBusy(true);
    api
      .setVersion(session.id, version)
      .then((s) => {
        onUpdate(s);
        toast.success(`Preferred version set to ${version.toUpperCase()}.`);
      })
      .catch((e: unknown) => toast.error(`Couldn't set version: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setBusy(false));
  };

  const btnCls = (v: PreferredVersion) =>
    `rounded border px-3 py-1 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 ${
      session.preferred_version === v
        ? 'border-custom-green bg-custom-green text-white'
        : 'border-gray-600 text-gray-200 enabled:hover:bg-gray-700'
    }`;

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-gray-700 bg-gray-800/60 p-3 text-sm">
      <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
        Preferred version for {session.voice_display}
      </span>
      <div className="flex gap-2">
        <button type="button" disabled={busy || readOnly} onClick={() => pick('v2')} className={btnCls('v2')}>
          V2
        </button>
        <button type="button" disabled={busy || readOnly} onClick={() => pick('v3')} className={btnCls('v3')}>
          V3
        </button>
      </div>
      {session.preferred_version === null && <span className="text-xs text-gray-500">Not picked yet</span>}
      {busy && <span className="text-xs text-gray-500">saving…</span>}
    </div>
  );
};

export default PreferredVersionControl;
