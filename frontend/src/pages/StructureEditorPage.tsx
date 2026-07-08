import { useCallback, useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import Modal from 'react-modal';
import { toast } from 'react-toastify';
import { api, ApiError, type StructureOpResult, type TripStructure } from '../api';
import NavBar from '../components/NavBar';
import { MODAL_STYLE } from '../modalStyle';

/** Admin scene-STRUCTURE editor: add/remove/reorder scenes, swap video (same-footage
 * vs re-key), static-image refs, direct categories. Every op writes STAGING
 * immediately (no session buffering) and is refused while an active session exists
 * on the trip. Scene media is positional — the amber warnings after structural ops
 * are load-bearing: audio must be re-staged before finalise/publish. */
const StructureEditorPage = () => {
  const { tripId = '' } = useParams<{ tripId: string }>();
  const [st, setSt] = useState<TripStructure | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  // Modals
  const [addOpen, setAddOpen] = useState(false);
  const [addPos, setAddPos] = useState(0);
  const [addUrl, setAddUrl] = useState('');
  const [addStatic, setAddStatic] = useState(false);
  const [addSceneId, setAddSceneId] = useState('');
  const [swapTarget, setSwapTarget] = useState<number | null>(null);
  const [swapUrl, setSwapUrl] = useState('');
  const [swapRekey, setSwapRekey] = useState(false);
  const [imagesTarget, setImagesTarget] = useState<number | null>(null);
  const [imagesText, setImagesText] = useState('');
  const [catsText, setCatsText] = useState('');
  const [removeTarget, setRemoveTarget] = useState<number | null>(null);

  const load = useCallback(() => {
    api
      .getStructure(tripId)
      .then((s) => {
        setSt(s);
        setCatsText(s.categories.join(', '));
      })
      .catch((e: unknown) => setError(e instanceof ApiError ? e.detail || e.code : 'Failed to load structure'));
  }, [tripId]);

  useEffect(() => {
    load();
  }, [load]);

  const applyResult = (r: StructureOpResult) => {
    setSt(r.structure);
    setCatsText(r.structure.categories.join(', '));
    setWarnings(r.warnings);
    r.warnings.forEach((w) => toast.warn(w, { autoClose: 12000 }));
  };

  const run = (p: Promise<StructureOpResult>, done?: () => void) => {
    setBusy(true);
    p.then((r) => {
      applyResult(r);
      done?.();
    })
      .catch((e: unknown) => {
        if (e instanceof ApiError && (e.code === 'state_changed' || e.code === 'active_session')) {
          toast.error(e.detail);
          load();
        } else {
          toast.error(`Failed: ${e instanceof ApiError ? e.detail : 'network error'}`);
        }
      })
      .finally(() => setBusy(false));
  };

  const move = (index: number, delta: number) => {
    if (!st) return;
    const order = st.scenes.map((s) => s.index);
    const j = index + delta;
    if (j < 0 || j >= order.length) return;
    [order[index], order[j]] = [order[j], order[index]];
    run(api.structureReorder(tripId, order, st.base));
  };

  if (error) {
    return (
      <>
        <NavBar title="Structure" backTo="/staging" backLabel="All trips" />
        <p className="mx-auto max-w-review px-4 py-8 text-red-300">{error}</p>
      </>
    );
  }
  if (!st) {
    return (
      <>
        <NavBar title="Structure" backTo="/staging" backLabel="All trips" />
        <p className="mx-auto max-w-review px-4 py-8 text-gray-400">Loading…</p>
      </>
    );
  }

  return (
    <>
      <NavBar title={`Structure · ${st.title}`} subtitle={`${st.trip_id} — direct staging edits`} backTo="/staging" backLabel="All trips" />
      <main className="mx-auto max-w-review space-y-4 px-4 py-6">
        <div className="rounded border border-amber-700/60 bg-amber-900/15 p-3 text-xs text-amber-200">
          Structural edits write staging <span className="font-medium">immediately</span> and are refused while a
          review session is active on this trip. Scene media (mp3/ogg/subtitles) is positional — after
          add/remove/reorder the audio stage must be re-run before finalise/publish.
        </div>

        {warnings.length > 0 && (
          <div className="rounded border border-amber-700 bg-amber-900/25 p-3 text-xs text-amber-100">
            {warnings.map((w, i) => (
              <p key={i}>⚠ {w}</p>
            ))}
          </div>
        )}

        {/* Categories */}
        <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
          <h2 className="mb-2 text-sm font-semibold text-white">Trip categories ({st.tripgroup_id})</h2>
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="text"
              value={catsText}
              onChange={(e) => setCatsText(e.target.value)}
              placeholder="Comma-separated categories"
              className="w-full max-w-lg rounded border border-gray-700 bg-gray-900 px-2 py-1 text-base sm:text-sm"
            />
            <button
              type="button"
              disabled={busy}
              onClick={() => run(api.structureCategories(tripId, catsText.split(',').map((c) => c.trim()).filter(Boolean)))}
              className="rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              Save categories
            </button>
          </div>
          <p className="mt-1 text-[11px] text-gray-500">
            Sets the live list verbatim (including level tags). Note: an approve re-derives the semantic set from
            the description’s “Trip Type:” line — keep them consistent.
          </p>
        </section>

        {/* Scenes */}
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-white">Scenes ({st.scenes.length})</h2>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setAddPos(st.scenes.length);
                setAddUrl('');
                setAddStatic(false);
                setAddSceneId('');
                setAddOpen(true);
              }}
              className="rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              + Add scene
            </button>
          </div>

          {st.scenes.map((s, i) => (
            <div key={s.scene_id ?? `i${s.index}`} className="flex flex-wrap items-center gap-3 rounded-lg border border-gray-700 bg-gray-800/60 p-3">
              <div className="flex flex-col gap-1">
                <button type="button" disabled={busy || i === 0} onClick={() => move(i, -1)} className="rounded border border-gray-600 px-2 py-0.5 text-xs text-gray-300 hover:bg-gray-700 disabled:opacity-30" title="Move up">▲</button>
                <button type="button" disabled={busy || i === st.scenes.length - 1} onClick={() => move(i, 1)} className="rounded border border-gray-600 px-2 py-0.5 text-xs text-gray-300 hover:bg-gray-700 disabled:opacity-30" title="Move down">▼</button>
              </div>
              {s.thumb_url ? (
                <img src={s.thumb_url} alt="" loading="lazy" className="h-16 w-28 rounded border border-gray-700 object-cover" />
              ) : (
                <div className="flex h-16 w-28 items-center justify-center rounded border border-gray-800 bg-black/40 text-[10px] text-gray-600">
                  {s.is_static_image ? '360 still' : 'no thumb'}
                </div>
              )}
              <div className="min-w-0 flex-1">
                <p className="text-sm text-gray-200">
                  <span className="mr-2 rounded bg-gray-700 px-1.5 py-0.5 text-[11px]">#{s.index}</span>
                  {s.title || <span className="text-gray-500">(no title)</span>}
                </p>
                <p className="truncate text-[11px] text-gray-500" title={s.video_url ?? ''}>
                  {s.scene_id ? <span className="text-sky-400">{s.scene_id}</span> : <span className="text-amber-400">no sceneId</span>}
                  {' · '}
                  {s.video_url || '—'}
                </p>
                {s.static_images.length > 0 && (
                  <p className="truncate text-[11px] text-gray-500">overlays: {s.static_images.join(', ')}</p>
                )}
              </div>
              <div className="flex shrink-0 flex-wrap gap-2">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setSwapTarget(i);
                    setSwapUrl(s.video_url ?? '');
                    setSwapRekey(false);
                  }}
                  className="rounded border border-gray-600 px-2 py-1 text-xs text-gray-300 hover:bg-gray-700"
                >
                  Video…
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setImagesTarget(i);
                    setImagesText(s.static_images.join(', '));
                  }}
                  className="rounded border border-gray-600 px-2 py-1 text-xs text-gray-300 hover:bg-gray-700"
                >
                  Overlays…
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setRemoveTarget(i)}
                  className="rounded border border-red-800 px-2 py-1 text-xs text-red-300 hover:bg-red-900/30"
                >
                  Remove
                </button>
              </div>
            </div>
          ))}
        </section>

        {/* Recent ops */}
        {st.recent_ops.length > 0 && (
          <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
            <h2 className="mb-2 text-sm font-semibold text-white">Recent structural edits</h2>
            <ul className="space-y-1 text-[11px] text-gray-400">
              {st.recent_ops.map((o, i) => (
                <li key={i}>
                  {new Date(o.at * 1000).toLocaleString()} · <span className="text-gray-300">{o.op}</span> by {o.by} ·{' '}
                  <span className="break-all text-gray-500">{JSON.stringify(o.payload)}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <p className="text-xs text-gray-500">
          Text/audio for scenes is authored in a normal review session (
          <Link to="/staging" className="underline">open the trip</Link> after structural changes).
        </p>
      </main>

      {/* Add scene */}
      <Modal isOpen={addOpen} onRequestClose={() => !busy && setAddOpen(false)} style={MODAL_STYLE} contentLabel="Add scene">
        <h2 className="mb-2 text-sm font-semibold">Add scene</h2>
        <label className="mb-2 block text-xs text-gray-400">
          Position (0–{st.scenes.length})
          <input type="number" min={0} max={st.scenes.length} value={addPos} onChange={(e) => setAddPos(Number(e.target.value))} className="mt-1 w-24 rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm" />
        </label>
        <label className="mb-2 block text-xs text-gray-400">
          videoUrl
          <input type="text" value={addUrl} onChange={(e) => setAddUrl(e.target.value)} placeholder="https://player.vimeo.com/… or file stem" className="mt-1 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm" />
        </label>
        <label className="mb-2 flex items-center gap-2 text-xs text-gray-400">
          <input type="checkbox" checked={addStatic} onChange={(e) => setAddStatic(e.target.checked)} /> static 360 still (no video)
        </label>
        <label className="mb-3 block text-xs text-gray-400">
          Existing sceneId (optional — reuse a registry atom instead of minting)
          <input type="text" value={addSceneId} onChange={(e) => setAddSceneId(e.target.value)} placeholder="s20240824-140508" className="mt-1 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm" />
        </label>
        <div className="flex justify-end gap-2">
          <button type="button" disabled={busy} onClick={() => setAddOpen(false)} className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700">Cancel</button>
          <button
            type="button"
            disabled={busy || (!addUrl.trim() && !addStatic)}
            onClick={() =>
              run(
                api.structureAdd(tripId, addPos, st.base, {
                  ...(addUrl.trim() ? { video_url: addUrl.trim() } : {}),
                  is_static: addStatic,
                  ...(addSceneId.trim() ? { scene_id: addSceneId.trim() } : {}),
                }),
                () => setAddOpen(false),
              )
            }
            className="rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            Add scene
          </button>
        </div>
      </Modal>

      {/* Swap / edit video */}
      <Modal isOpen={swapTarget !== null} onRequestClose={() => !busy && setSwapTarget(null)} style={MODAL_STYLE} contentLabel="Scene video">
        <h2 className="mb-2 text-sm font-semibold">Scene #{swapTarget} video</h2>
        <label className="mb-3 block text-xs text-gray-400">
          videoUrl
          <input type="text" value={swapUrl} onChange={(e) => setSwapUrl(e.target.value)} autoFocus className="mt-1 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm" />
        </label>
        <label className="mb-1 flex items-start gap-2 text-xs text-gray-300">
          <input type="checkbox" checked={swapRekey} onChange={(e) => setSwapRekey(e.target.checked)} className="mt-0.5" />
          <span>
            This is a <span className="font-medium">different scene</span> (re-key: new sceneId is assigned; translations
            keyed to the old scene fall back to English until re-authored).
          </span>
        </label>
        <p className="mb-3 text-[11px] text-gray-500">
          Unchecked = same footage, new encode/URL fix — the sceneId is kept and the registry gains the videoId.
        </p>
        <div className="flex justify-end gap-2">
          <button type="button" disabled={busy} onClick={() => setSwapTarget(null)} className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700">Cancel</button>
          <button
            type="button"
            disabled={busy || !swapUrl.trim()}
            onClick={() => run(api.structureSwapVideo(tripId, swapTarget!, swapUrl.trim(), swapRekey, st.base), () => setSwapTarget(null))}
            className={`rounded px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50 ${swapRekey ? 'bg-amber-600' : 'bg-custom-green'}`}
          >
            {swapRekey ? 'Swap & re-key' : 'Update videoUrl'}
          </button>
        </div>
      </Modal>

      {/* Overlays */}
      <Modal isOpen={imagesTarget !== null} onRequestClose={() => !busy && setImagesTarget(null)} style={MODAL_STYLE} contentLabel="Scene overlays">
        <h2 className="mb-2 text-sm font-semibold">Scene #{imagesTarget} flat overlays</h2>
        <p className="mb-2 text-xs text-gray-400">
          Comma-separated JPG filenames. These are references — the files must exist in the trip’s image trees to
          render in the app.
        </p>
        <input type="text" value={imagesText} onChange={(e) => setImagesText(e.target.value)} placeholder="a.jpg, suncake.jpg" autoFocus className="mb-3 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm" />
        <div className="flex justify-end gap-2">
          <button type="button" disabled={busy} onClick={() => setImagesTarget(null)} className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700">Cancel</button>
          <button
            type="button"
            disabled={busy}
            onClick={() => run(api.structureStaticImages(tripId, imagesTarget!, imagesText.split(',').map((f) => f.trim()).filter(Boolean), st.base), () => setImagesTarget(null))}
            className="rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            Save overlays
          </button>
        </div>
      </Modal>

      {/* Remove confirm */}
      <Modal isOpen={removeTarget !== null} onRequestClose={() => !busy && setRemoveTarget(null)} style={MODAL_STYLE} contentLabel="Remove scene">
        <h2 className="mb-2 text-sm font-semibold">Remove scene #{removeTarget}?</h2>
        <p className="mb-3 text-xs text-gray-400">
          Removes the scene from the staging trip immediately (its localization entry is dropped, later scenes are
          renumbered, and the Scenes-registry use is released). Positional audio for every later scene will no
          longer line up — the audio stage must be re-run.
        </p>
        <div className="flex justify-end gap-2">
          <button type="button" disabled={busy} onClick={() => setRemoveTarget(null)} className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700">Cancel</button>
          <button
            type="button"
            disabled={busy}
            onClick={() => run(api.structureRemove(tripId, removeTarget!, st.base), () => setRemoveTarget(null))}
            className="rounded bg-red-700 px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            Remove scene
          </button>
        </div>
      </Modal>
    </>
  );
};

export default StructureEditorPage;
