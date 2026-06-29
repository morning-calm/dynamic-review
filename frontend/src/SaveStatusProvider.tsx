import { useCallback, useMemo, useRef, useState, type ReactNode } from 'react';
import { SaveStatusContext, type SaveCoordinator, type SaveState } from './saveStatusContext';

/** Provides a save coordinator whose `state` reflects all in-flight autosaves. */
export const SaveStatusProvider = ({ children }: { children: ReactNode }) => {
  const pending = useRef(0);
  const [state, setState] = useState<SaveState>('idle');

  const begin = useCallback(() => {
    pending.current += 1;
    setState('saving');
  }, []);

  const end = useCallback((ok: boolean) => {
    pending.current = Math.max(0, pending.current - 1);
    if (!ok) {
      setState('error');
      return;
    }
    if (pending.current === 0) setState('saved');
  }, []);

  const value = useMemo<SaveCoordinator>(() => ({ begin, end, state }), [begin, end, state]);

  return <SaveStatusContext.Provider value={value}>{children}</SaveStatusContext.Provider>;
};
