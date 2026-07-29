import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'react-toastify';
import { api, ApiError, type Field, type Waveform } from '../api';

interface WaveformEditorProps {
  field: Field;
  sid: string;
  onFieldUpdate: (f: Field) => void;
}

/**
 * "Edit waveform" — the direct-manipulation audio editor.
 *
 * Every other tool in this app addresses the audio THROUGH THE TEXT: you put a caret in
 * the narration box and the backend maps it to a time via Whisper (English) or the MMS
 * aligner (CJK). That is what makes those tools safe — they refuse to cut anywhere but a
 * real pause — and also what makes them coarse and slow.
 *
 * Here the reviewer sees the audio and says exactly where. No text, no aligner. That
 * buys precision (a click lands on the millisecond you can see) and speed (no
 * transcription), and it costs the guardrails: these edits WILL cut through a word if
 * that is where you put them. The backstop is the one every audio op already has —
 * each edit archives a version (Undo steps back through them) and clears the coverage
 * gate, so the clip must be listened through again before it can be marked done.
 *
 * Interaction: click to place the playhead, drag to select a span. With a span:
 * Silence / Delete / Cut (then click where to drop it). With no span, the Insert
 * buttons open a gap at the playhead.
 */
const HEIGHT = 96;

const fmt = (t: number) => {
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${m}:${s.toFixed(2).padStart(5, '0')}`;
};

const WaveformEditor = ({ field, sid, onFieldUpdate }: WaveformEditorProps) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [wave, setWave] = useState<Waveform | null>(null);
  const [busy, setBusy] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [sel, setSel] = useState<{ a: number; b: number } | null>(null);
  const [playhead, setPlayhead] = useState(0);
  // A span that has been "cut" and is waiting for the reviewer to click where it goes.
  // Nothing has happened to the audio yet — the move is one server op, applied on drop.
  const [pending, setPending] = useState<{ start: number; end: number } | null>(null);
  // The visible time window (zoom): null = the whole clip. Refined edits — especially
  // on a phone, where the full clip is ~350 px wide — need to magnify the region
  // being worked on; all pointer/edit coordinates go through this window.
  const [view, setView] = useState<{ t0: number; t1: number } | null>(null);
  const dragging = useRef(false);
  const dragFrom = useRef(0);
  // Active pointers (for two-finger pinch zoom) and, while pinching, the audio time
  // anchored under each finger — the window is solved so those times stay put.
  const pointers = useRef<Map<number, number>>(new Map());
  const pinchTimes = useRef<Map<number, number> | null>(null);

  const workingUrl = field.audio.working;

  const dur = wave?.duration || 1;
  const v0 = view?.t0 ?? 0;
  const v1 = view?.t1 ?? dur;
  // Keep ≥ ~10 envelope buckets on screen — beyond that there is no more data to show.
  const minWin = Math.max(0.1, (10 * dur) / (wave?.buckets || 1600));

  const clampView = useCallback(
    (t0: number, t1: number): { t0: number; t1: number } | null => {
      const w = Math.max(minWin, t1 - t0);
      if (w >= dur) return null; // zoomed all the way out
      const a = Math.max(0, Math.min(t0, dur - w));
      return { t0: a, t1: a + w };
    },
    [dur, minWin],
  );

  const zoomBy = (factor: number) => {
    if (!wave) return;
    const w = (v1 - v0) / factor;
    const centre = sel ? (sel.a + sel.b) / 2 : cursor > 0 ? cursor : (v0 + v1) / 2;
    setView(clampView(centre - w / 2, centre + w / 2));
  };

  const panBy = (frac: number) => {
    if (!view) return;
    const shift = (v1 - v0) * frac;
    setView(clampView(v0 + shift, v1 + shift));
  };

  const load = useCallback(async () => {
    try {
      setWave(await api.waveform(sid, field.fid));
    } catch {
      setWave(null);
    }
    // An edit can change the clip's length — snap back to the whole clip so the
    // window is never out of range (and the result of the edit is visible).
    setView(null);
  }, [sid, field.fid]);

  // Re-fetch whenever the working take changes (the URL carries its content hash), so
  // the envelope on screen is always the audio the next edit will be applied to.
  useEffect(() => {
    void load();
  }, [load, workingUrl]);

  // --- draw ---------------------------------------------------------------------
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv || !wave) return;
    const dpr = window.devicePixelRatio || 1;
    const cssW = cv.clientWidth;
    cv.width = Math.max(1, Math.floor(cssW * dpr));
    cv.height = Math.floor(HEIGHT * dpr);
    const ctx = cv.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, HEIGHT);

    // Draw through the SAME window the pointer maths uses (v0/v1) — one source of truth,
    // or a zoomed click lands somewhere other than where it was drawn.
    const win = Math.max(0.001, v1 - v0);
    const x = (t: number) => ((t - v0) / win) * cssW;
    const mid = HEIGHT / 2;

    ctx.fillStyle = '#0b1220';
    ctx.fillRect(0, 0, cssW, HEIGHT);

    if (sel) {
      ctx.fillStyle = 'rgba(56,189,248,0.20)'; // sky-400 @ 20%
      ctx.fillRect(x(sel.a), 0, Math.max(1, x(sel.b) - x(sel.a)), HEIGHT);
    }

    // The envelope: one min..max bar per bucket, drawn over the bucket's time span so
    // it stays solid when zoomed in (a bucket can be wider than one pixel).
    ctx.fillStyle = pending ? '#64748b' : '#94a3b8';
    const n = wave.buckets;
    const bDur = dur / n;
    const i0 = Math.max(0, Math.floor(v0 / bDur));
    const i1 = Math.min(n, Math.ceil(v1 / bDur));
    for (let i = i0; i < i1; i += 1) {
      const lo = wave.peaks[i * 2] / 127;
      const hi = wave.peaks[i * 2 + 1] / 127;
      const px0 = x(i * bDur);
      const px1 = x((i + 1) * bDur);
      const y0 = mid - hi * mid * 0.95;
      const y1 = mid - lo * mid * 0.95;
      ctx.fillRect(px0, y0, Math.max(1, px1 - px0), Math.max(1, y1 - y0));
    }

    ctx.strokeStyle = '#334155'; // zero line
    ctx.beginPath();
    ctx.moveTo(0, mid + 0.5);
    ctx.lineTo(cssW, mid + 0.5);
    ctx.stroke();

    const tick = (t: number, colour: string, w = 2) => {
      ctx.strokeStyle = colour;
      ctx.lineWidth = w;
      ctx.beginPath();
      ctx.moveTo(x(t), 0);
      ctx.lineTo(x(t), HEIGHT);
      ctx.stroke();
    };
    if (playhead > 0) tick(playhead, '#22c55e', 1); // where the audio is playing
    tick(cursor, pending ? '#f59e0b' : '#e2e8f0'); // where the next edit lands
  }, [wave, sel, cursor, playhead, pending, dur, v0, v1]);

  // --- pointer ------------------------------------------------------------------
  const fracAt = (clientX: number): number => {
    const cv = canvasRef.current;
    if (!cv) return 0;
    const r = cv.getBoundingClientRect();
    return Math.min(1, Math.max(0, (clientX - r.left) / r.width));
  };

  const timeAt = (clientX: number): number => v0 + fracAt(clientX) * (v1 - v0);

  const onDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    // A PRIMARY pointer down starts a fresh gesture with no other finger already down,
    // so anything still in the map is stale — a pointerup we never received because the
    // canvas was unmounted mid-gesture (a failed waveform fetch swaps it for the
    // "Loading…" box). Left behind, that ghost id makes the very next single touch look
    // like a second finger and starts a bogus pinch, permanently.
    if (e.isPrimary) {
      pointers.current.clear();
      pinchTimes.current = null;
    }
    if (busy || !wave) return;
    pointers.current.set(e.pointerId, e.clientX);
    e.currentTarget.setPointerCapture(e.pointerId);
    if (pointers.current.size === 2) {
      // Second finger down → pinch zoom. Anchor the audio time under each finger and
      // abandon any selection drag the first finger started.
      pinchTimes.current = new Map(
        [...pointers.current.entries()].map(([id, cx]) => [id, timeAt(cx)]),
      );
      dragging.current = false;
      setSel(null);
      return;
    }
    if (pinchTimes.current) return; // 3rd+ finger — ignore
    const t = timeAt(e.clientX);
    dragging.current = true;
    dragFrom.current = t;
    setCursor(t);
    setSel(null);
  };

  const onMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!wave) return;
    if (pointers.current.has(e.pointerId)) pointers.current.set(e.pointerId, e.clientX);
    const anchors = pinchTimes.current;
    if (anchors) {
      // Solve the window so both anchored times stay under their fingers:
      // t = t0 + frac·w for each finger → w and t0.
      const pts = [...pointers.current.entries()]
        .filter(([id]) => anchors.has(id))
        .slice(0, 2);
      if (pts.length < 2) return;
      const [[idA, xA], [idB, xB]] = pts;
      const fA = fracAt(xA);
      const fB = fracAt(xB);
      if (Math.abs(fB - fA) < 0.02) return; // fingers (nearly) together — unstable
      const tA = anchors.get(idA) as number;
      const tB = anchors.get(idB) as number;
      const w = (tB - tA) / (fB - fA);
      if (!Number.isFinite(w) || w <= 0) return;
      setView(clampView(tA - fA * w, tA - fA * w + w));
      return;
    }
    if (!dragging.current) return;
    const t = timeAt(e.clientX);
    const a = Math.min(dragFrom.current, t);
    const b = Math.max(dragFrom.current, t);
    setSel(b - a > 0.01 ? { a, b } : null);
    setCursor(t);
  };

  const onUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinchTimes.current = null;
    dragging.current = false;
  };

  // --- ops ----------------------------------------------------------------------
  const run = async (label: string, fn: () => Promise<Field>) => {
    setBusy(true);
    try {
      const updated = await fn();
      onFieldUpdate(updated);
      setSel(null);
      setPending(null);
      toast.success(`${label} — listen back to confirm.`);
    } catch (e) {
      if (e instanceof ApiError) toast.warn(e.detail);
      else toast.error(`${label} failed.`);
    } finally {
      setBusy(false);
    }
  };

  const insertAt = (seconds: number) =>
    run(`Inserted ${seconds}s of silence at ${fmt(cursor)}`, () =>
      api.waveInsertSilence(sid, field.fid, cursor, seconds),
    );

  const silenceSel = () =>
    sel && run('Silenced the selection', () => api.waveSilence(sid, field.fid, sel.a, sel.b));

  const deleteSel = () =>
    sel && run('Deleted the selection', () => api.waveDelete(sid, field.fid, sel.a, sel.b));

  // Cut is staged, not applied: the audio only changes when the reviewer clicks where the
  // span should land, so it is a single undoable server op rather than a delete + insert.
  const cutSel = () => {
    if (!sel) return;
    setPending({ start: sel.a, end: sel.b });
    toast.info('Now click where it should go, then “Drop here”.');
  };

  const dropHere = () =>
    pending &&
    run('Moved the selection', () => api.waveMove(sid, field.fid, pending.start, pending.end, cursor));

  // --- audition ------------------------------------------------------------------
  const playFromCursor = () => {
    const el = audioRef.current;
    if (!el) return;
    el.currentTime = sel ? sel.a : cursor;
    void el.play();
    if (sel) {
      const stopAt = sel.b;
      const tick = () => {
        if (!audioRef.current) return;
        if (audioRef.current.currentTime >= stopAt) audioRef.current.pause();
        else requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }
  };

  const btn =
    'rounded border px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-40 enabled:hover:bg-gray-700';
  // Zoom cluster: same look, slightly tighter — it lives inside the info row.
  const zbtn =
    'rounded border px-1.5 py-0.5 text-xs disabled:cursor-not-allowed disabled:opacity-40 enabled:hover:bg-gray-700';

  if (!wave) {
    return <div className="rounded border border-gray-700 bg-gray-900/40 p-3 text-xs text-gray-500">Loading waveform…</div>;
  }

  return (
    <div className="space-y-2 rounded border border-gray-700 bg-gray-900/40 p-3">
      <canvas
        ref={canvasRef}
        style={{ height: HEIGHT }}
        className="w-full cursor-text touch-none rounded border border-gray-800"
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
      />
      {workingUrl && (
        <audio
          ref={audioRef}
          src={workingUrl}
          preload="metadata"
          onTimeUpdate={(e) => setPlayhead(e.currentTarget.currentTime)}
          onEnded={() => setPlayhead(0)}
          className="hidden"
        />
      )}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-400">
        <span>
          Cursor <span className="font-mono text-gray-200">{fmt(cursor)}</span>
        </span>
        {sel && (
          <span className="text-sky-300">
            Selected <span className="font-mono">{fmt(sel.a)}</span>–<span className="font-mono">{fmt(sel.b)}</span> (
            {(sel.b - sel.a).toFixed(2)}s)
          </span>
        )}
        {pending && (
          <span className="text-amber-300">
            Cut {(pending.end - pending.start).toFixed(2)}s — click where it goes, then “Drop here”
          </span>
        )}
        <span className="ml-auto flex items-center gap-1">
          {view && (
            <span className="mr-1 hidden font-mono text-sky-400 sm:inline">
              {fmt(v0)}–{fmt(v1)}
            </span>
          )}
          <button
            type="button"
            onClick={() => zoomBy(2)}
            aria-label="Zoom in"
            title="Zoom in around the selection (or cursor) for finer edits — or pinch on the waveform"
            className={`${zbtn} border-gray-600 text-gray-200`}
          >
            🔍+
          </button>
          <button
            type="button"
            disabled={!view}
            onClick={() => zoomBy(0.5)}
            aria-label="Zoom out"
            title="Zoom out"
            className={`${zbtn} border-gray-600 text-gray-200`}
          >
            🔍−
          </button>
          <button
            type="button"
            disabled={!view}
            onClick={() => panBy(-0.4)}
            aria-label="Scroll the view earlier"
            title="Scroll the view earlier"
            className={`${zbtn} border-gray-600 text-gray-200`}
          >
            ◀
          </button>
          <button
            type="button"
            disabled={!view}
            onClick={() => panBy(0.4)}
            aria-label="Scroll the view later"
            title="Scroll the view later"
            className={`${zbtn} border-gray-600 text-gray-200`}
          >
            ▶
          </button>
          <button
            type="button"
            disabled={!view}
            onClick={() => setView(null)}
            title="Show the whole clip"
            className={`${zbtn} border-gray-600 text-gray-200`}
          >
            Fit
          </button>
          <span className="font-mono text-gray-500">{fmt(wave.duration)}</span>
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" disabled={busy} onClick={playFromCursor} className={`${btn} border-gray-600 text-gray-200`}>
          ▶ {sel ? 'Play selection' : 'Play from cursor'}
        </button>
        <span aria-hidden="true" className="h-5 w-px shrink-0 self-center bg-gray-600" />

        <button
          type="button"
          disabled={busy || Boolean(pending)}
          onClick={() => void insertAt(0.5)}
          title="Open a 0.5s gap at the cursor — exactly where you put it (no snapping to a pause)"
          className={`${btn} border-gray-600 text-gray-200`}
        >
          +0.5s
        </button>
        <button
          type="button"
          disabled={busy || Boolean(pending)}
          onClick={() => void insertAt(1)}
          title="Open a 1s gap at the cursor"
          className={`${btn} border-gray-600 text-gray-200`}
        >
          +1s
        </button>
        <button
          type="button"
          disabled={busy || Boolean(pending)}
          onClick={() => void insertAt(3)}
          title="Open a 3s gap at the cursor (the beginner-trip end pause)"
          className={`${btn} border-gray-600 text-gray-200`}
        >
          +3s
        </button>

        <span aria-hidden="true" className="h-5 w-px shrink-0 self-center bg-gray-600" />
        <button
          type="button"
          disabled={busy || !sel || Boolean(pending)}
          onClick={() => void silenceSel()}
          title="Blank the selection to silence, keeping the clip's length (a click, a cough, a breath)"
          className={`${btn} border-gray-600 text-gray-200`}
        >
          Silence
        </button>
        <button
          type="button"
          disabled={busy || !sel || Boolean(pending)}
          onClick={() => void deleteSel()}
          title="Remove the selection and close the gap"
          className={`${btn} border-gray-600 text-gray-200`}
        >
          Delete
        </button>
        <button
          type="button"
          disabled={busy || !sel || Boolean(pending)}
          onClick={cutSel}
          title="Lift the selection out, then click where it should go"
          className={`${btn} border-gray-600 text-gray-200`}
        >
          Cut &amp; move…
        </button>
        {pending && (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => void dropHere()}
              className={`${btn} border-custom-green text-custom-green`}
            >
              Drop here ({fmt(cursor)})
            </button>
            <button type="button" disabled={busy} onClick={() => setPending(null)} className={`${btn} border-gray-600 text-gray-400`}>
              Cancel
            </button>
          </>
        )}
        {busy && <span className="text-xs text-gray-500">working…</span>}
      </div>

      <p className="text-xs text-gray-500">
        Click to place the cursor, drag to select. Pinch (or 🔍+) to zoom in for fine placement. These edits go
        exactly where you put them — they can cut through a word, so listen back. Undo steps back through every
        change.
      </p>
    </div>
  );
};

export default WaveformEditor;
