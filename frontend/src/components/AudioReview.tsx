import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError, flushPlayedBeacon, type Field } from '../api';
import { useDebouncedCallback } from '../hooks';

interface AudioReviewProps {
  field: Field;
  sid: string;
  onFieldUpdate: (f: Field) => void;
}

type Range = [number, number];

const MERGE_GAP = 0.1; // seconds — join ranges within 100 ms
const MAX_STEP = 1.5; // a jump bigger than this is a seek, not contiguous play

const mergeRanges = (ranges: Range[]): Range[] => {
  if (ranges.length === 0) return [];
  const sorted = [...ranges].sort((a, b) => a[0] - b[0]);
  const merged: Range[] = [[sorted[0]![0], sorted[0]![1]]];
  for (let i = 1; i < sorted.length; i += 1) {
    const r = sorted[i]!;
    const last = merged[merged.length - 1]!;
    if (r[0] <= last[1] + MERGE_GAP) last[1] = Math.max(last[1], r[1]);
    else merged.push([r[0], r[1]]);
  }
  return merged;
};

const coveredSeconds = (ranges: Range[]): number =>
  ranges.reduce((sum, [s, e]) => sum + Math.max(0, e - s), 0);

/**
 * Plain audio row that reloads itself whenever its src URL changes. The backend
 * appends `?v=<hash>` that changes with content, so this guarantees a second
 * candidate/fallback take is never auditioned from a cached response.
 */
const AudioRow = ({ label, src }: { label: string; src: string | null }) => {
  const ref = useRef<HTMLAudioElement | null>(null);
  useEffect(() => {
    ref.current?.load();
  }, [src]);
  if (!src) return null;
  return (
    <div className="flex items-center gap-2">
      <span className="w-20 shrink-0 text-xs text-gray-400">{label}</span>
      <audio ref={ref} controls preload="none" src={src} className="h-8 w-full" />
    </div>
  );
};

/**
 * Players for original / working / candidate / fallback. Tracks contiguous
 * playback coverage of the WORKING take from `timeupdate` (ignoring seeks),
 * POSTs it to `/played`, and — keyed solely on the working URL changing —
 * reloads + resets coverage so Done can never unlock against audio never heard.
 */
const AudioReview = ({ field, sid, onFieldUpdate }: AudioReviewProps) => {
  const workingEl = useRef<HTMLAudioElement | null>(null);
  const coveredRanges = useRef<Range[]>(mergeRanges(field.played_coverage as Range[]));
  const lastTime = useRef(0);
  const seeking = useRef(false);

  const [coverage, setCoverage] = useState<Range[]>(coveredRanges.current);
  const [duration, setDuration] = useState<number>(0);

  // The field is in its pristine state → listening to the ORIGINAL fully can mark it
  // done ("the original is correct"). Once any edit/regeneration happens, the original
  // is just a reference and Done is gated on the working take instead (server-enforced).
  const untouched =
    field.current_text === field.original_text && !field.audio.candidate && field.versions.length === 0;

  // ---- Original-track coverage (only meaningful while untouched) ----
  const origEl = useRef<HTMLAudioElement | null>(null);
  const origRanges = useRef<Range[]>(mergeRanges(field.original_played_coverage as Range[]));
  const origLast = useRef(0);
  const origSeeking = useRef(false);
  const [origCoverage, setOrigCoverage] = useState<Range[]>(origRanges.current);
  const [origDuration, setOrigDuration] = useState<number>(0);

  // True while coverage sits in the debounce window un-POSTed — flushed via a
  // keepalive beacon if the tab hides first (mobile screen-lock/backgrounding).
  const origDirty = useRef(false);

  const { call: postOrigCall } = useDebouncedCallback((ranges: Range[]) => {
    origDirty.current = false;
    api
      .postPlayed(sid, field.fid, ranges, 'original')
      .then((res) => {
        origRanges.current = res.played_coverage as Range[];
        setOrigCoverage(res.played_coverage as Range[]);
        onFieldUpdate({ ...field, can_mark_done: res.can_mark_done });
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status !== 0) console.warn('played(original) failed', e.detail);
      });
  }, 700);

  const handleOrigTimeUpdate = () => {
    const el = origEl.current;
    if (!el) return;
    const t = el.currentTime;
    if (origSeeking.current) {
      origSeeking.current = false;
      origLast.current = t;
      return;
    }
    if (t > origLast.current && t - origLast.current < MAX_STEP) {
      origRanges.current = mergeRanges([...origRanges.current, [origLast.current, t]]);
      setOrigCoverage(origRanges.current);
      origDirty.current = true;
      postOrigCall(origRanges.current);
    }
    origLast.current = t;
  };

  const origPct = useMemo(() => {
    if (!origDuration) return 0;
    return Math.min(100, Math.round((coveredSeconds(origCoverage) / origDuration) * 100));
  }, [origCoverage, origDuration]);

  // Latest played_coverage, read inside the URL-change effect WITHOUT being a
  // dependency (we must not reset/reload when our own /played POST mutates it).
  const playedCoverageRef = useRef(field.played_coverage);
  playedCoverageRef.current = field.played_coverage;

  const workingDirty = useRef(false);

  const { call: postPlayedCall, cancel: postPlayedCancel } = useDebouncedCallback((ranges: Range[]) => {
    workingDirty.current = false;
    api
      .postPlayed(sid, field.fid, ranges)
      .then((res) => {
        coveredRanges.current = res.played_coverage as Range[];
        setCoverage(res.played_coverage as Range[]);
        // Reflect server-merged coverage + the authoritative can_mark_done flag.
        onFieldUpdate({ ...field, played_coverage: res.played_coverage, can_mark_done: res.can_mark_done });
      })
      .catch((e: unknown) => {
        // Coverage is best-effort; the server re-checks on /flag. Log only.
        if (e instanceof ApiError && e.status !== 0) console.warn('played POST failed', e.detail);
      });
  }, 700);

  // The backend changes `?v=<hash>` on the working URL whenever the take's
  // content changes OR coverage is reset server-side (combine/import/revert/edit).
  // Keying the reset+reload on that URL closes the "Done unlocks on audio never
  // heard" hole: a fresh take always starts from zero coverage and a real reload.
  const workingUrl = field.audio.working;
  useEffect(() => {
    postPlayedCancel(); // S1: stop an in-flight debounce reposting stale ranges
    workingDirty.current = false;
    coveredRanges.current = mergeRanges(playedCoverageRef.current as Range[]);
    setCoverage(coveredRanges.current);
    lastTime.current = 0;
    seeking.current = false;
    setDuration(0);
    workingEl.current?.load();
  }, [workingUrl, postPlayedCancel]);

  const handleTimeUpdate = () => {
    const el = workingEl.current;
    if (!el) return;
    const t = el.currentTime;
    if (seeking.current) {
      seeking.current = false;
      lastTime.current = t;
      return;
    }
    if (t > lastTime.current && t - lastTime.current < MAX_STEP) {
      coveredRanges.current = mergeRanges([...coveredRanges.current, [lastTime.current, t]]);
      setCoverage(coveredRanges.current);
      workingDirty.current = true;
      postPlayedCall(coveredRanges.current);
    }
    lastTime.current = t;
  };

  const pct = useMemo(() => {
    if (!duration) return 0;
    return Math.min(100, Math.round((coveredSeconds(coverage) / duration) * 100));
  }, [coverage, duration]);

  // ---- Mobile coverage hardening ----
  // Screen Wake Lock (progressive enhancement, failure-silent): phones stop firing
  // `timeupdate` when the screen locks, stalling the Done-gate coverage — hold the
  // screen awake while a gated track is actually playing.
  const wakeLock = useRef<{ release: () => Promise<void> } | null>(null);
  const wakeLockPending = useRef(false); // guards a double request while one is in flight
  const anyTrackPlaying = () => [workingEl.current, origEl.current].some((el) => el && !el.paused && !el.ended);
  const acquireWakeLock = () => {
    type WakeLockApi = { request: (type: 'screen') => Promise<{ release: () => Promise<void> }> };
    const wl = (navigator as Navigator & { wakeLock?: WakeLockApi }).wakeLock;
    if (!wl || wakeLock.current || wakeLockPending.current) return;
    wakeLockPending.current = true;
    wl.request('screen')
      .then((lock) => {
        wakeLockPending.current = false;
        wakeLock.current = lock;
        releaseWakeLockIfIdle(); // playback may have stopped while the request was in flight
      })
      .catch(() => {
        /* denied/unsupported — playback still works, the screen may just sleep */
        wakeLockPending.current = false;
      });
  };
  const releaseWakeLockIfIdle = () => {
    if (anyTrackPlaying() || !wakeLock.current) return;
    void wakeLock.current.release().catch(() => {});
    wakeLock.current = null;
  };

  // When the tab hides, the wake lock is auto-released by the browser and any
  // coverage still inside the 700ms debounce window would be lost with the tab —
  // flush it via a keepalive beacon (the server merges idempotently).
  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') {
        wakeLock.current = null; // browsers auto-release on hide
        if (workingDirty.current) {
          workingDirty.current = false;
          flushPlayedBeacon(sid, field.fid, coveredRanges.current);
        }
        if (origDirty.current) {
          origDirty.current = false;
          flushPlayedBeacon(sid, field.fid, origRanges.current, 'original');
        }
      } else {
        if (anyTrackPlaying()) acquireWakeLock();
      }
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      void wakeLock.current?.release().catch(() => {});
      wakeLock.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid, field.fid]);

  return (
    <div className="space-y-2 rounded border border-gray-700 bg-gray-900/40 p-2">
      {field.audio.original && (
        <div className="flex items-center gap-2">
          <span className="w-20 shrink-0 text-xs text-gray-400">Original</span>
          <audio
            ref={origEl}
            controls
            preload="none"
            src={field.audio.original}
            onTimeUpdate={handleOrigTimeUpdate}
            onPlay={acquireWakeLock}
            onPause={releaseWakeLockIfIdle}
            onEnded={releaseWakeLockIfIdle}
            onSeeking={() => {
              origSeeking.current = true;
            }}
            onSeeked={() => {
              if (origEl.current) origLast.current = origEl.current.currentTime;
            }}
            onLoadedMetadata={() => setOrigDuration(origEl.current?.duration ?? 0)}
            className="h-8 w-full"
          />
        </div>
      )}

      {untouched && field.audio.original && (
        <div className="flex items-center gap-2 pl-0 text-[11px] text-gray-400 sm:pl-[5.5rem]">
          <div className="h-1.5 flex-1 overflow-hidden rounded bg-gray-700">
            <div
              className={`h-full ${field.can_mark_done ? 'bg-custom-green' : 'bg-sky-500'}`}
              style={{ width: `${origPct}%` }}
            />
          </div>
          <span>{field.can_mark_done ? 'fully heard' : `original ${origPct}% (done at 95%)`}</span>
        </div>
      )}

      {workingUrl && (
        <div className="flex items-center gap-2">
          <span className="w-20 shrink-0 text-xs text-gray-400">Working</span>
          <audio
            ref={workingEl}
            controls
            preload="metadata"
            src={workingUrl}
            onTimeUpdate={handleTimeUpdate}
            onPlay={acquireWakeLock}
            onPause={releaseWakeLockIfIdle}
            onEnded={releaseWakeLockIfIdle}
            onSeeking={() => {
              seeking.current = true;
            }}
            onSeeked={() => {
              if (workingEl.current) lastTime.current = workingEl.current.currentTime;
            }}
            onLoadedMetadata={() => setDuration(workingEl.current?.duration ?? 0)}
            className="h-8 w-full"
          />
        </div>
      )}

      <AudioRow label="Candidate" src={field.audio.candidate} />
      <AudioRow label="Fallback" src={field.audio.fallback} />

      {workingUrl && (
        <div className="flex items-center gap-2 text-[11px] text-gray-400">
          <div className="h-1.5 flex-1 overflow-hidden rounded bg-gray-700">
            <div
              className={`h-full ${field.can_mark_done ? 'bg-custom-green' : 'bg-amber-500'}`}
              style={{ width: `${pct}%` }}
            />
          </div>
          <span>{field.can_mark_done ? 'fully heard' : `played ${pct}% (done at 95%)`}</span>
        </div>
      )}

      {field.splice_confidence !== null && (
        <p className="text-[11px] text-gray-500">Splice confidence: {(field.splice_confidence * 100).toFixed(0)}%</p>
      )}

      {field.versions.length > 0 && (
        <details className="text-[11px] text-gray-500">
          <summary className="cursor-pointer hover:text-gray-300">
            {field.versions.length} archived take{field.versions.length === 1 ? '' : 's'}
          </summary>
          <div className="mt-1 space-y-1">
            {field.versions.map((v) => (
              <AudioRow key={v.label} label={v.label} src={v.url} />
            ))}
          </div>
        </details>
      )}
    </div>
  );
};

// Memoised: a keystroke in one scene must not re-render the other ~20 scenes.
export default memo(AudioReview);
