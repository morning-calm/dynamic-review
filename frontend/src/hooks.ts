import { useCallback, useEffect, useMemo, useRef, useState, type TextareaHTMLAttributes } from 'react';

/**
 * Returns a stable debounced wrapper around `fn`. The latest `fn` is always
 * used (kept in a ref) so callers can pass an inline closure without resetting
 * the timer. `flush()` invokes immediately with the last args; `cancel()` drops
 * a pending call.
 */
export const useDebouncedCallback = <A extends unknown[]>(
  fn: (...args: A) => void,
  delayMs: number,
): { call: (...args: A) => void; flush: () => void; cancel: () => void } => {
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastArgs = useRef<A | null>(null);

  const cancel = useCallback(() => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const flush = useCallback(() => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    if (lastArgs.current !== null) {
      const args = lastArgs.current;
      lastArgs.current = null;
      fnRef.current(...args);
    }
  }, []);

  const call = useCallback(
    (...args: A) => {
      lastArgs.current = args;
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        timer.current = null;
        lastArgs.current = null;
        fnRef.current(...args);
      }, delayMs);
    },
    [delayMs],
  );

  // Clear any pending timer on unmount.
  useEffect(() => cancel, [cancel]);

  // Stable object so effects that depend on the returned handle don't churn.
  return useMemo(() => ({ call, flush, cancel }), [call, flush, cancel]);
};

/** Reactive `window.matchMedia` — re-renders when the query starts/stops matching.
 * SSR-safe (returns false when `matchMedia` is unavailable). */
export const useMediaQuery = (query: string): boolean => {
  const get = () =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(query).matches
      : false;
  const [matches, setMatches] = useState(get);
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [query]);
  return matches;
};

export interface CapturedSelection {
  start: number;
  end: number;
  /** The substring the range covered when captured — compared against the live
   * text to detect a stale capture. Empty for a collapsed caret. */
  text: string;
}

/** The selection-event handlers the hook needs spread onto its textarea. */
export type SelectionBind = Pick<
  TextareaHTMLAttributes<HTMLTextAreaElement>,
  'onSelect' | 'onMouseUp' | 'onTouchEnd' | 'onKeyUp'
>;

/**
 * Persists the reviewer's last selection/caret in a textarea so the audio
 * selection tools (highlight / alt / trim-noise / pause) still see it after the
 * textarea blurs. Desktop keeps a textarea's selection after blur, but iOS
 * collapses it the moment a tool button is tapped — the live read the tools
 * used to do comes up empty on touch. Capture rules:
 *  - a non-empty range is recorded from any selection event;
 *  - a collapsed caret is recorded ONLY from a gesture ending inside the
 *    textarea (mouseup / touchend / keyup), never from `select` alone — the
 *    iOS blur-collapse must not wipe a captured range.
 * The capture is invalidated when the text it indexes changes under it
 * (captured substring no longer matches) or when `resetKey` (the working-take
 * URL — combine/regenerate/undo re-baselines what offsets mean) changes.
 */
export const useTextSelection = (sourceText: string, resetKey?: unknown) => {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  const capturedRef = useRef<CapturedSelection | null>(null);
  const [selection, setSelection] = useState<CapturedSelection | null>(null);

  const setCaptured = useCallback((sel: CapturedSelection | null) => {
    capturedRef.current = sel;
    setSelection(sel);
  }, []);

  const record = useCallback(
    (allowCaret: boolean) => {
      const el = ref.current;
      if (!el) return;
      const { selectionStart: start, selectionEnd: end } = el;
      if (start == null || end == null) return;
      if (start !== end) setCaptured({ start, end, text: el.value.slice(start, end) });
      else if (allowCaret) setCaptured({ start, end, text: '' });
    },
    [setCaptured],
  );

  const bind: SelectionBind = useMemo(
    () => ({
      onSelect: () => record(false),
      onMouseUp: () => record(true),
      onTouchEnd: () => record(true),
      onKeyUp: () => record(true),
    }),
    [record],
  );

  // Stale-capture invalidation: the text the offsets index into changed.
  useEffect(() => {
    const sel = capturedRef.current;
    if (!sel) return;
    const stale =
      sel.start !== sel.end
        ? sourceText.slice(sel.start, sel.end) !== sel.text
        : sel.start > sourceText.length;
    if (stale) setCaptured(null);
  }, [sourceText, setCaptured]);

  // A new working take re-baselines the audio the offsets map onto — drop the capture.
  const firstReset = useRef(true);
  useEffect(() => {
    if (firstReset.current) {
      firstReset.current = false;
      return;
    }
    setCaptured(null);
  }, [resetKey, setCaptured]);

  /** Same contract the tools always read: the live selection when it survives
   * (desktop), else the captured one (touch), else the live caret. */
  const getSelectionRange = useCallback((): { start: number; end: number } | null => {
    const el = ref.current;
    if (el && el.selectionStart !== el.selectionEnd) return { start: el.selectionStart, end: el.selectionEnd };
    const cap = capturedRef.current;
    if (cap) return { start: cap.start, end: cap.end };
    return el ? { start: el.selectionStart, end: el.selectionEnd } : null;
  }, []);

  const clearSelection = useCallback(() => setCaptured(null), [setCaptured]);

  return { ref, bind, getSelectionRange, selection, clearSelection };
};
