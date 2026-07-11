import { useState } from 'react';
import { toast } from 'react-toastify';
import { api, ApiError, type Field } from '../api';

/** A filename minted by the per-scene download (`sessions.field_download_name`). Used to
 * tell "this is another field's take" from "this is some other mp3 entirely". */
const SCENE_TAKE = /_scene\d+_(SceneDesc|questionKey|questionOption\d+)\.mp3$/;

interface ImportMp3Props {
  field: Field;
  sid: string;
  onUpdate: (f: Field) => void;
  /** Compact styling for the per-field control row on the Review page. */
  compact?: boolean;
}

/**
 * Replace this field's WORKING take with an mp3 edited outside the app — the other half of
 * the per-scene download (admin: download → fix in a desktop editor → import here).
 *
 * NOT to be confused with "Create new", whose Import mp3 only ATTACHES a take for someone
 * else to action; this one is what actually installs the file as the new working master
 * (archiving the previous take, clearing coverage and re-locking Done, server-side).
 */
const ImportMp3 = ({ field, sid, onUpdate, compact = false }: ImportMp3Props) => {
  const [busy, setBusy] = useState(false);

  // Wrong-slot guard: a per-scene download names each file for the field it came from, so
  // a file carrying ANOTHER field's name means a scene's takes are going in one slot over
  // (SceneDesc into questionKey, option 1 into option 2) — silent and nasty, since both
  // are valid mp3s.
  //
  // It fires ONLY on a name that looks like one of ours but belongs elsewhere. Any other
  // filename passes silently: a hand-made or renamed file is legitimate, and the whole-trip
  // zip (Changes page) extracts as `3.mp3` / `3_q.mp3`, so warning on those would nag on
  // every import of that long-standing flow and train admins to click the dialog away.
  const isAnotherFieldsTake = (file: File): boolean =>
    !!field.download_name && file.name !== field.download_name && SCENE_TAKE.test(file.name);

  const importFile = (file: File) => {
    setBusy(true);
    api
      .importMp3(sid, field.fid, file)
      .then((updated) => {
        onUpdate(updated);
        toast.success('Imported as the new working master — re-listen before marking done.');
      })
      .catch((e: unknown) => toast.error(`Import failed: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setBusy(false));
  };

  const onPick = (file: File | undefined) => {
    if (!file) return;
    if (
      isAnotherFieldsTake(file) &&
      !window.confirm(
        `"${file.name}" was downloaded for a DIFFERENT field.\n\n` +
          `This field expects: ${field.download_name}\n\n` +
          'Importing it here would replace this field’s audio with another field’s. Import anyway?',
      )
    ) {
      return;
    }
    importFile(file);
  };

  const cls = compact
    ? 'inline-flex cursor-pointer items-center rounded border border-gray-600 px-3 py-2 text-xs text-gray-200 hover:bg-gray-700 sm:px-2 sm:py-1'
    : 'inline-flex cursor-pointer items-center rounded border border-gray-600 px-2 py-1 text-xs text-gray-200 hover:bg-gray-700';

  return (
    <label
      className={`${cls} ${busy ? 'opacity-50' : ''}`}
      title={
        field.download_name
          ? `Replace this field's working audio with an edited mp3 (expects ${field.download_name})`
          : "Replace this field's working audio with an edited mp3"
      }
    >
      {busy ? 'Importing…' : 'Import edited MP3'}
      <input
        type="file"
        accept="audio/mpeg,.mp3"
        className="hidden"
        disabled={busy}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          onPick(file);
        }}
      />
    </label>
  );
};

export default ImportMp3;
