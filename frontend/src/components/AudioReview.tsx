import { memo, useCallback, useEffect, useMemo, useRef, useState, type RefObject, type SyntheticEvent } from 'react';
import { api, ApiError, flushPlayedBeacon, type Field } from '../api';
import { useAuth } from '../authContext';
import { useDebouncedCallback } from '../hooks';
import ImportMp3 from './ImportMp3';

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

// ---- Custom transport ----------------------------------------------------
// The native <audio controls> bar is fully seekable, so a take can be dragged
// past without listening — the exact skip the Done-gate exists to prevent. This
// transport replaces it: play/pause, restart, and back-5s/back-10s buttons to
// RE-listen, but the position bar is display-only (no forward seek). All the
// coverage/wake-lock bookkeeping is unchanged — the same <audio> element is kept
// (just without `controls`) and every event handler is passed straight through.

type AudioEvt = SyntheticEvent<HTMLAudioElement>;

const fmtTime = (s: number): string => {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, '0')}`;
};

const iconCls = 'inline-block align-middle';
const ChevronLeft = ({ double = false }: { double?: boolean }) => (
  <svg className={iconCls} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {double ? (
      <>
        <path d="M11 18l-6-6 6-6" />
        <path d="M18 18l-6-6 6-6" />
      </>
    ) : (
      <path d="M15 18l-6-6 6-6" />
    )}
  </svg>
);
const IconRestart = () => (
  <svg className={iconCls} width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M3 12a9 9 0 1 0 2.6-6.3" />
    <path d="M3 3v4h4" />
  </svg>
);
const IconPlay = () => (
  <svg className={iconCls} width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M8 5v14l11-7z" />
  </svg>
);
const IconPause = () => (
  <svg className={iconCls} width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M6 5h4v14H6zM14 5h4v14h-4z" />
  </svg>
);

interface TransportProps {
  label: string;
  /** Always non-null — every call site conditions the render on the URL existing. */
  src: string;
  /** Parent-owned ref to the underlying <audio> — the coverage bookkeeping reads it. */
  elRef?: RefObject<HTMLAudioElement | null>;
  preload?: 'none' | 'metadata' | 'auto';
  onTimeUpdate?: (e: AudioEvt) => void;
  onPlay?: () => void;
  onPause?: () => void;
  onEnded?: () => void;
  onSeeking?: () => void;
  onSeeked?: () => void;
  onLoadedMetadata?: (e: AudioEvt) => void;
}

const Transport = ({
  label, src, elRef, preload = 'none',
  onTimeUpdate, onPlay, onPause, onEnded, onSeeking, onSeeked, onLoadedMetadata,
}: TransportProps) => {
  const internal = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [cur, setCur] = useState(0);
  const [dur, setDur] = useState(0);

  // Callback ref: keep the element in our internal ref AND the parent's (so the
  // caller's coverage handlers can read currentTime/duration off the same node).
  const setRef = useCallback(
    (el: HTMLAudioElement | null) => {
      internal.current = el;
      if (elRef) elRef.current = el;
    },
    [elRef],
  );

  // The backend appends `?v=<hash>` that changes with content, so a changed src is a
  // new take — reload and reset the transport UI (position must not carry over).
  useEffect(() => {
    internal.current?.load();
    setPlaying(false);
    setCur(0);
  }, [src]);

  const toggle = () => {
    const el = internal.current;
    if (!el) return;
    if (el.paused) void el.play().catch(() => {});
    else el.pause();
  };
  const rewind = (s: number) => {
    const el = internal.current;
    if (el) el.currentTime = Math.max(0, (el.currentTime || 0) - s);
  };
  const restart = () => {
    const el = internal.current;
    if (!el) return;
    el.currentTime = 0;
    void el.play().catch(() => {});
  };

  const pct = dur > 0 ? Math.min(100, (cur / dur) * 100) : 0;
  const btn =
    'flex items-center gap-0.5 rounded px-1.5 py-1 text-gray-300 enabled:hover:bg-gray-600 enabled:hover:text-white';

  return (
    <div className="flex items-center gap-2">
      <span className="w-16 shrink-0 text-xs text-gray-400 sm:w-20">{label}</span>
      <audio
        ref={setRef}
        preload={preload}
        src={src}
        className="hidden"
        onTimeUpdate={(e) => {
          setCur(e.currentTarget.currentTime);
          onTimeUpdate?.(e);
        }}
        onDurationChange={(e) => setDur(e.currentTarget.duration || 0)}
        onLoadedMetadata={(e) => {
          setDur(e.currentTarget.duration || 0);
          onLoadedMetadata?.(e);
        }}
        onPlay={() => {
          setPlaying(true);
          onPlay?.();
        }}
        onPause={() => {
          setPlaying(false);
          onPause?.();
        }}
        onEnded={() => {
          setPlaying(false);
          onEnded?.();
        }}
        onSeeking={() => onSeeking?.()}
        onSeeked={() => onSeeked?.()}
      />
      <div className="flex flex-1 items-center gap-0.5 rounded-full bg-gray-700 px-2 py-1">
        <button type="button" className={btn} onClick={() => rewind(10)} aria-label="Back 10 seconds" title="Back 10 seconds">
          <ChevronLeft double />
          <span className="text-[10px] tabular-nums">10</span>
        </button>
        <button type="button" className={btn} onClick={() => rewind(5)} aria-label="Back 5 seconds" title="Back 5 seconds">
          <ChevronLeft />
          <span className="text-[10px] tabular-nums">5</span>
        </button>
        <button type="button" className={btn} onClick={restart} aria-label="Restart from the beginning" title="Restart from the beginning">
          <IconRestart />
        </button>
        <button
          type="button"
          className={`${btn} ml-0.5 text-custom-green`}
          onClick={toggle}
          aria-label={playing ? 'Pause' : 'Play'}
          title={playing ? 'Pause' : 'Play'}
        >
          {playing ? <IconPause /> : <IconPlay />}
        </button>
        {/* Position bar is DISPLAY-ONLY — forward-seeking is intentionally disabled so a
            take can't be skipped past; the rewind buttons above are how you re-listen. */}
        <div className="mx-1 h-1.5 flex-1 overflow-hidden rounded-full bg-gray-600">
          <div className="h-full rounded-full bg-gray-300" style={{ width: `${pct}%` }} />
        </div>
        <span className="shrink-0 text-[10px] tabular-nums text-gray-400">
          {fmtTime(cur)}&thinsp;/&thinsp;{dur ? fmtTime(dur) : '—'}
        </span>
      </div>
    </div>
  );
};

/** Auxiliary take (candidate / fallback / archived version) — the same transport,
 * no coverage wiring. Reloads on src change via the transport's own effect. */
const AudioRow = ({ label, src }: { label: string; src: string | null }) =>
  src ? <Transport label={label} src={src} /> : null;

/**
 * Players for original / working / candidate / fallback. Tracks contiguous
 * playback coverage of the WORKING take from `timeupdate` (ignoring seeks),
 * POSTs it to `/played`, and — keyed solely on the working URL changing —
 * reloads + resets coverage so Done can never unlock against audio never heard.
 */
const AudioReview = ({ field, sid, onFieldUpdate }: AudioReviewProps) => {
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
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
        <Transport
          label="Original"
          src={field.audio.original}
          elRef={origEl}
          preload="none"
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
        />
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
        <Transport
          label="Working"
          src={workingUrl}
          elRef={workingEl}
          preload="metadata"
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
        />
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

      {/* Admin: the return leg of "Download scene audio" — the fixed mp3 goes back in AT the
          field it belongs to, so the slot is chosen by WHERE you click, not by filename.
          Deliberately outside the readOnly `inert` wrappers: an admin fixing audio on a
          `submitted` session is exactly the case this exists for (the backend allows it). */}
      {isAdmin && field.has_audio && (
        <div className="pt-1">
          <ImportMp3 field={field} sid={sid} onUpdate={onFieldUpdate} compact />
        </div>
      )}
    </div>
  );
};

// Memoised: a keystroke in one scene must not re-render the other ~20 scenes.
export default memo(AudioReview);
