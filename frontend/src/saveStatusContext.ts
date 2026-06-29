import { createContext, useContext } from 'react';

export type SaveState = 'idle' | 'saving' | 'saved' | 'error';

export interface SaveCoordinator {
  /** Call when an autosave request starts. */
  begin: () => void;
  /** Call when an autosave request finishes; pass false on failure. */
  end: (ok: boolean) => void;
  state: SaveState;
}

export const SaveStatusContext = createContext<SaveCoordinator | null>(null);

/** Always returns a usable coordinator (no-op when no provider is mounted). */
export const useSaveCoordinator = (): SaveCoordinator => {
  const ctx = useContext(SaveStatusContext);
  if (ctx) return ctx;
  return { begin: () => {}, end: () => {}, state: 'idle' };
};
