import { useCallback, useEffect, useMemo, useRef } from 'react';

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
