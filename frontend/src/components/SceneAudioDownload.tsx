import { useState } from 'react';
import { toast } from 'react-toastify';
import { api, ApiError } from '../api';
import { useAuth } from '../authContext';
import { saveBlob } from '../saveBlob';

interface SceneAudioDownloadProps {
  sid: string;
  sceneIndex: number;
  tripId: string;
}

/**
 * ADMIN ONLY (the backend 403s reviewers): pull this scene's takes down to fix a reviewer's
 * `edit_required` flag in a desktop audio editor. Each mp3 in the zip is named for the field
 * it came from, and each field's own "Import edited MP3" button takes it back — so a take
 * can't quietly go back into the wrong slot.
 */
const SceneAudioDownload = ({ sid, sceneIndex, tripId }: SceneAudioDownloadProps) => {
  const { user } = useAuth();
  const [busy, setBusy] = useState(false);
  if (user?.role !== 'admin') return null;

  const download = () => {
    setBusy(true);
    api
      .downloadSceneZip(sid, sceneIndex)
      .then((blob) => saveBlob(blob, `${tripId}_scene${sceneIndex}_audio.zip`))
      .catch((e: unknown) =>
        toast.error(`Download failed: ${e instanceof ApiError ? e.detail : 'network error'}`),
      )
      .finally(() => setBusy(false));
  };

  return (
    <button
      type="button"
      disabled={busy}
      onClick={download}
      title="Admin: download this scene's audio (each file named for its field) to edit offline, then re-import it at that field"
      className="rounded border border-gray-600 px-2 py-1 text-xs text-gray-300 hover:bg-gray-700 disabled:opacity-50"
    >
      {busy ? 'Downloading…' : 'Download scene audio'}
    </button>
  );
};

export default SceneAudioDownload;
