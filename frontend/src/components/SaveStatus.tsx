import type { SaveState } from '../saveStatusContext';

const LABELS: Record<SaveState, string> = {
  idle: 'No changes yet',
  saving: 'Saving…',
  saved: 'All changes saved',
  error: 'Save failed — retrying…',
};

const DOT: Record<SaveState, string> = {
  idle: 'bg-gray-500',
  saving: 'bg-amber-400 animate-pulse',
  saved: 'bg-custom-green',
  error: 'bg-red-500',
};

/** Persistent save-status indicator shown in the nav bar. */
const SaveStatus = ({ state }: { state: SaveState }) => (
  <div className="flex items-center gap-2 text-xs text-gray-300" aria-live="polite">
    <span className={`inline-block h-2.5 w-2.5 rounded-full ${DOT[state]}`} />
    <span>{LABELS[state]}</span>
  </div>
);

export default SaveStatus;
