import { useMemo, useState } from 'react';
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
 * auditioning the side-by-side players on each field. Picking collapses the
 * A/B players into a single editable take; switching (or clearing the pick to
 * re-listen side-by-side) is allowed but DROPS any audio edits made on the
 * picked take — the reviewer is warned first. Text/script edits always survive.
 * Replaces NarrationControls in the `_ZH` review header.
 */
const PreferredVersionControl = ({ session, onUpdate, readOnly = false }: Props) => {
  const [busy, setBusy] = useState(false);

  // Any audio field stepped past its v0 (can_undo) has audio edits that a switch/clear
  // would delete. Text/script edits live on the field rows and are never touched.
  const hasAudioEdits = useMemo(
    () => [...session.trip_fields, ...session.scenes.flatMap((s) => s.fields)].some((f) => f.can_undo),
    [session],
  );

  const apply = (version: PreferredVersion | null, okMsg: string) => {
    setBusy(true);
    api
      .setVersion(session.id, version)
      .then((s) => {
        onUpdate(s);
        toast.success(okMsg);
      })
      .catch((e: unknown) => toast.error(`Couldn't set version: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setBusy(false));
  };

  const pick = (version: PreferredVersion) => {
    if (busy || readOnly || session.preferred_version === version) return;
    const cur = session.preferred_version;
    if (cur && hasAudioEdits) {
      const ok = window.confirm(
        `You have made changes to the ${cur.toUpperCase()} audio. Switching to ${version.toUpperCase()} ` +
          `will delete all those audio changes (text edits are kept). Continue?`,
      );
      if (!ok) return;
    }
    apply(version, `Preferred version set to ${version.toUpperCase()}.`);
  };

  const clearPick = () => {
    if (busy || readOnly || !session.preferred_version) return;
    const cur = session.preferred_version.toUpperCase();
    const msg = hasAudioEdits
      ? `You have made changes to the ${cur} audio. Going back to the V2/V3 side-by-side ` +
        `listening will delete all those audio changes (text edits are kept). Continue?`
      : `Clear the ${cur} pick and go back to the V2/V3 side-by-side listening?`;
    if (!window.confirm(msg)) return;
    apply(null, 'Pick cleared — V2 and V3 can be auditioned side-by-side again.');
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
      {session.preferred_version !== null && (
        <button
          type="button"
          disabled={busy || readOnly}
          onClick={clearPick}
          title="Undo the pick and listen to V2 and V3 side-by-side again (audio edits made on the picked take are deleted; text edits are kept)"
          className="text-xs text-gray-500 underline enabled:hover:text-gray-300 disabled:opacity-40"
        >
          Clear pick (listen to both again)
        </button>
      )}
      {busy && <span className="text-xs text-gray-500">saving…</span>}
    </div>
  );
};

export default PreferredVersionControl;
