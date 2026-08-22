import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'react-toastify';
import {
  api,
  ApiError,
  type CreditProposals,
  type CreditsDoc,
  type FinalStaticImages,
} from '../api';

const errText = (e: unknown, fallback: string): string =>
  e instanceof ApiError ? e.detail || e.code : fallback;

/** One overlay's timing row: appear/disappear (0.1s floats) + set-from-playhead + save. */
const OverlayRow = ({
  tripId,
  sceneIndex,
  overlay,
  duration,
  playhead,
  onSaved,
}: {
  tripId: string;
  sceneIndex: number;
  overlay: FinalStaticImages['scenes'][number]['overlays'][number];
  duration: number | null;
  playhead: () => number;
  onSaved: () => void;
}) => {
  const [appear, setAppear] = useState<number | ''>(overlay.appear ?? '');
  const [disappear, setDisappear] = useState<number | ''>(overlay.disappear ?? '');
  const [busy, setBusy] = useState(false);
  // Bumped after replace/revert so the <img> refetches (the URL is otherwise
  // identical and the browser serves its cached copy — "replace did nothing").
  const [imgBust, setImgBust] = useState(0);
  useEffect(() => {
    setAppear(overlay.appear ?? '');
    setDisappear(overlay.disappear ?? '');
  }, [overlay]);

  const dirty = appear !== (overlay.appear ?? '') || disappear !== (overlay.disappear ?? '');

  const save = () => {
    if (appear === '' || disappear === '') return;
    setBusy(true);
    api
      .setFinalImageTiming(tripId, {
        scene_index: sceneIndex,
        filename: overlay.filename,
        appear: Number(appear),
        disappear: Number(disappear),
      })
      .then((r) => {
        toast.success(`${overlay.filename}: ${r.appear}s → ${r.disappear}s written to staging`);
        r.warnings.forEach((w) => toast.warn(`${overlay.filename}: ${w}`));
        onSaved();
      })
      .catch((e: unknown) => toast.error(errText(e, 'Timing save failed')))
      .finally(() => setBusy(false));
  };

  const upload = (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    api
      .replaceFinalOverlay(tripId, overlay.filename, file)
      .then((r) => {
        toast.success(
          `${overlay.filename} replaced on R2${r.replace_job ? '; canonical distribution queued for the workstation (stage10 replace)' : ''}. Revert undoes it.`,
        );
        setImgBust(Date.now());
        onSaved();
      })
      .catch((e: unknown) => toast.error(errText(e, 'Replace failed')))
      .finally(() => setBusy(false));
  };

  const revert = () => {
    setBusy(true);
    api
      .revertFinalOverlay(tripId, overlay.filename)
      .then((r) => {
        toast.success(
          r.mode === 'restored_previous'
            ? `${overlay.filename}: previous image restored${r.replace_job ? ' (re-distribution queued)' : ''}.`
            : `${overlay.filename}: replacement removed — the original local image serves again.`,
        );
        setImgBust(Date.now());
        onSaved();
      })
      .catch((e: unknown) => toast.error(errText(e, 'Nothing to revert')))
      .finally(() => setBusy(false));
  };

  // Timeline strip: the overlay's on-screen span against the scene audio.
  const frac = (v: number) => (duration ? Math.min(100, (v / duration) * 100) : 0);

  return (
    <div className="rounded border border-gray-700 bg-gray-900/40 p-2">
      <div className="flex flex-wrap items-start gap-3">
        <img
          src={imgBust ? `${overlay.url}${overlay.url.includes('?') ? '&' : '?'}v=${imgBust}` : overlay.url}
          alt={overlay.filename}
          className="max-h-28 rounded border border-gray-700"
          onError={(e) => {
            e.currentTarget.style.opacity = '0.25';
          }}
        />
        <div className="min-w-64 flex-1 space-y-2">
          <p className="text-xs font-medium text-gray-200">{overlay.filename}</p>
          {duration !== null && appear !== '' && disappear !== '' && (
            <div className="relative h-2 rounded bg-gray-700">
              <div
                className="absolute h-2 rounded bg-indigo-500/80"
                style={{ left: `${frac(Number(appear))}%`, width: `${Math.max(1, frac(Number(disappear)) - frac(Number(appear)))}%` }}
                title={`${appear}s → ${disappear}s of ${duration.toFixed(1)}s`}
              />
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <label className="text-gray-400">
              appear{' '}
              <input
                type="number"
                min={0}
                step={0.1}
                value={appear}
                onChange={(e) => setAppear(e.target.value === '' ? '' : Number(e.target.value))}
                className="w-20 rounded border border-gray-600 bg-gray-900 px-1.5 py-1 text-gray-100"
              />
            </label>
            <button
              type="button"
              onClick={() => setAppear(Math.round(playhead() * 10) / 10)}
              className="rounded border border-gray-600 px-2 py-1 text-gray-300 hover:bg-gray-700"
              title="Set appear to the audio playhead"
            >
              = playhead
            </button>
            <label className="text-gray-400">
              disappear{' '}
              <input
                type="number"
                min={0}
                step={0.1}
                value={disappear}
                onChange={(e) => setDisappear(e.target.value === '' ? '' : Number(e.target.value))}
                className="w-20 rounded border border-gray-600 bg-gray-900 px-1.5 py-1 text-gray-100"
              />
            </label>
            <button
              type="button"
              onClick={() => setDisappear(Math.round(playhead() * 10) / 10)}
              className="rounded border border-gray-600 px-2 py-1 text-gray-300 hover:bg-gray-700"
              title="Set disappear to the audio playhead"
            >
              = playhead
            </button>
            <button
              type="button"
              disabled={busy || !dirty || appear === '' || disappear === ''}
              onClick={save}
              className="rounded bg-custom-green px-2.5 py-1 font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              Save timing
            </button>
            <label className="cursor-pointer rounded border border-gray-600 px-2 py-1 text-gray-300 hover:bg-gray-700">
              {busy ? '…' : 'Replace image…'}
              <input
                type="file"
                accept="image/jpeg,image/png"
                className="hidden"
                disabled={busy}
                onChange={(e) => {
                  upload(e.target.files?.[0]);
                  e.target.value = '';
                }}
              />
            </label>
            <button
              type="button"
              disabled={busy}
              onClick={revert}
              className="rounded border border-gray-600 px-2 py-1 text-gray-300 hover:bg-gray-700 disabled:opacity-50"
              title="Undo the last Replace image (restores the previous copy, or removes the replacement so the original serves again)"
            >
              Revert
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

/** Check-5 body: per scene with staticImages[], the working narration audio +
 * the overlays' timing (targeted staging writes; house rules warn, never block),
 * plus the app's single Credits doc (append-only). */
const StaticImagesPanel = ({ tripId }: { tripId: string }) => {
  const [model, setModel] = useState<FinalStaticImages | null>(null);
  const [credits, setCredits] = useState<CreditsDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [durations, setDurations] = useState<Record<number, number>>({});
  // Live playhead per scene, one decimal — the native controls only show mm:ss,
  // and the timing inputs are 0.1s-granular, so the exact time must be readable.
  const [playheads, setPlayheads] = useState<Record<number, number>>({});
  const audioRefs = useRef<Record<number, HTMLAudioElement | null>>({});
  const [creditHeader, setCreditHeader] = useState('');
  const [creditEntry, setCreditEntry] = useState('');
  const [creditBusy, setCreditBusy] = useState(false);
  const [proposals, setProposals] = useState<CreditProposals | null>(null);
  const [propBusy, setPropBusy] = useState(false);

  const load = useCallback(() => {
    api
      .getFinalStaticImages(tripId)
      .then(setModel)
      .catch((e: unknown) => setError(errText(e, 'Failed to load static images')));
    api
      .getFinalCredits()
      .then(setCredits)
      .catch(() => {});
  }, [tripId]);
  useEffect(load, [load]);

  const addCredit = () => {
    if (!creditHeader.trim() || !creditEntry.trim()) return;
    setCreditBusy(true);
    api
      .addFinalCredit(creditHeader.trim(), creditEntry.trim())
      .then((r) => {
        toast.success('Credit added to staging Credits — publish via the publish_credits job.');
        setCredits(r);
        setCreditEntry('');
      })
      .catch((e: unknown) => toast.error(errText(e, 'Add credit failed')))
      .finally(() => setCreditBusy(false));
  };

  const loadProposals = () => {
    setPropBusy(true);
    api
      .creditProposals(tripId)
      .then(setProposals)
      .catch((e: unknown) => toast.error(errText(e, 'Could not read attributions')))
      .finally(() => setPropBusy(false));
  };

  /** Add one proposed line under the proposal header, then refresh both lists so
   * the row flips to "already in the Credits doc". */
  const addProposal = (entry: string) => {
    if (!proposals) return;
    setCreditBusy(true);
    api
      .addFinalCredit(proposals.header, entry)
      .then((r) => {
        toast.success(`Added under “${proposals.header}”.`);
        setCredits(r);
        setProposals((p) =>
          p
            ? {
                ...p,
                proposals: p.proposals.map((x) =>
                  x.entry === entry ? { ...x, status: 'already_added' } : x,
                ),
              }
            : p,
        );
      })
      .catch((e: unknown) => toast.error(errText(e, 'Add credit failed')))
      .finally(() => setCreditBusy(false));
  };

  if (error) return <p className="text-xs text-rose-400">{error}</p>;
  if (!model) return <p className="text-xs text-gray-500">Loading…</p>;

  return (
    <div className="space-y-4">
      {model.scenes.length === 0 && (
        <p className="text-xs text-gray-500">No scene carries staticImages[] on this trip.</p>
      )}
      <p className="text-xs text-gray-500">
        House rules (warn only): appear ≥ {model.rules.min_appear}s · on screen{' '}
        {model.rules.min_display}–{model.rules.max_display}s · ≥ {model.rules.gap}s between
        overlays.
      </p>
      {model.scenes.map((s) => (
        <div key={s.scene_index} className="space-y-2 rounded border border-gray-700 bg-gray-900/30 p-3">
          <p className="flex items-center gap-3 text-xs font-semibold text-gray-200">
            Scene {s.scene_index}
            <span className="font-mono text-[11px] font-normal tabular-nums text-gray-400">
              t = {(playheads[s.scene_index] ?? 0).toFixed(1)}s
              {durations[s.scene_index] !== undefined &&
                ` / ${durations[s.scene_index].toFixed(1)}s`}
            </span>
          </p>
          <audio
            controls
            preload="metadata"
            src={s.audio_url}
            className="h-8 w-full"
            ref={(el) => {
              audioRefs.current[s.scene_index] = el;
            }}
            onLoadedMetadata={(e) => {
              // Read duration NOW: React nulls e.currentTarget after the handler,
              // and the setState updater runs later — reading it inside the
              // updater crashed the whole page when many players loaded at once.
              const dur = e.currentTarget.duration;
              setDurations((d) => ({ ...d, [s.scene_index]: dur }));
            }}
            onTimeUpdate={(e) => {
              const t = Math.round(e.currentTarget.currentTime * 10) / 10;
              setPlayheads((p) =>
                p[s.scene_index] === t ? p : { ...p, [s.scene_index]: t },
              );
            }}
            onSeeked={(e) => {
              const t = Math.round(e.currentTarget.currentTime * 10) / 10;
              setPlayheads((p) => ({ ...p, [s.scene_index]: t }));
            }}
          />
          <p className="whitespace-pre-wrap text-xs text-gray-400">{s.narration}</p>
          {s.overlays.map((o) => (
            <OverlayRow
              key={o.filename}
              tripId={tripId}
              sceneIndex={s.scene_index}
              overlay={o}
              duration={durations[s.scene_index] ?? null}
              playhead={() => audioRefs.current[s.scene_index]?.currentTime ?? 0}
              onSaved={load}
            />
          ))}
        </div>
      ))}

      {/* Credits — the app's ONE credits button (CustomizableMenus/Credits) */}
      <div className="rounded border border-gray-700 bg-gray-900/30 p-3">
        <p className="mb-1 text-xs font-semibold text-gray-200">
          Credits (the app’s single credits button)
        </p>
        <p className="mb-2 text-xs text-gray-500">
          Format is the VR app’s own — headers with entry lines, append-only. An
          externally-sourced image (Wikimedia etc.) needs its attribution added here; reaches
          production via the Publisher’s <code>publish_credits</code> job.
        </p>
        {credits && credits.credits.length > 0 && (
          <div className="mb-2 max-h-56 space-y-2 overflow-y-auto">
            {credits.credits.map((b) => (
              <div key={b.header}>
                <p className="text-xs font-semibold text-gray-300">{b.header}</p>
                <ul className="ml-3 list-disc text-xs text-gray-400">
                  {b.entries.map((e) => (
                    <li key={e}>{e}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
        {credits && !credits.exists && (
          <p className="mb-2 text-xs text-amber-300">
            No CustomizableMenus/Credits doc on staging yet — the first add creates it in the
            app’s format.
          </p>
        )}
        {/* Auto-fill from the drafting webfetch attribution sidecars */}
        <div className="mb-2">
          <button
            type="button"
            disabled={propBusy}
            onClick={loadProposals}
            className="rounded border border-teal-700 px-2.5 py-1 text-xs text-teal-300 hover:bg-teal-900/30 disabled:opacity-50"
            title="Build credit lines from each overlay's drafting attribution sidecar (Source/Author/Licence)"
          >
            {propBusy ? 'Reading attributions…' : 'Propose credits from drafting data'}
          </button>
          {proposals && (
            <div className="mt-2 space-y-1.5">
              {proposals.proposals.length === 0 && (
                <p className="text-xs text-gray-500">This trip has no overlays.</p>
              )}
              {proposals.proposals.map((p) => (
                <div key={p.filename} className="flex flex-wrap items-start gap-2 text-xs">
                  <span className="w-44 truncate text-gray-300" title={p.filename}>
                    {p.filename}
                  </span>
                  {p.status === 'proposed' && (
                    <>
                      <span className="min-w-64 flex-1 break-all text-gray-400">{p.entry}</span>
                      <button
                        type="button"
                        disabled={creditBusy}
                        onClick={() => addProposal(p.entry)}
                        className="rounded bg-teal-700 px-2 py-0.5 text-[11px] font-semibold text-white hover:bg-teal-600 disabled:opacity-50"
                        title={`Add under “${proposals.header}”`}
                      >
                        + add
                      </button>
                    </>
                  )}
                  {p.status === 'already_added' && (
                    <span className="text-emerald-400">already in the Credits doc</span>
                  )}
                  {p.status === 'needs_hand_edit' && (
                    <span className="min-w-64 flex-1 break-all text-amber-300">
                      incomplete attribution — edit by hand: {p.entry || p.detail}
                    </span>
                  )}
                  {p.status === 'no_attribution' && (
                    <span className="text-gray-500">{p.detail}</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            list="credit-headers"
            value={creditHeader}
            onChange={(e) => setCreditHeader(e.target.value)}
            placeholder="Header (e.g. Scotland)"
            className="w-40 rounded border border-gray-600 bg-gray-900 px-2 py-1 text-xs text-gray-100"
          />
          <datalist id="credit-headers">
            {(credits?.credits ?? []).map((b) => (
              <option key={b.header} value={b.header} />
            ))}
          </datalist>
          <input
            value={creditEntry}
            onChange={(e) => setCreditEntry(e.target.value)}
            placeholder="Credit line (image — author, licence, source)"
            className="min-w-72 flex-1 rounded border border-gray-600 bg-gray-900 px-2 py-1 text-xs text-gray-100"
          />
          <button
            type="button"
            disabled={creditBusy || !creditHeader.trim() || !creditEntry.trim()}
            onClick={addCredit}
            className="rounded bg-custom-green px-2.5 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            Add credit
          </button>
        </div>
      </div>
    </div>
  );
};

export default StaticImagesPanel;
