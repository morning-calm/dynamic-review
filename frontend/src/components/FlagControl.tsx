import { useState } from 'react';
import { toast } from 'react-toastify';
import { api, ApiError, type Field, type FlagValue } from '../api';

interface FlagControlProps {
  field: Field;
  sid: string;
  onFieldUpdate: (f: Field) => void;
}

const FLAG_BADGE: Record<FlagValue, { label: string; cls: string }> = {
  none: { label: 'Unreviewed', cls: 'bg-gray-700 text-gray-200' },
  done: { label: 'Done', cls: 'bg-custom-green text-white' },
  edit_required: { label: 'Edit required', cls: 'bg-amber-600 text-white' },
};

/** done / edit-required / clear, with done gated on `can_mark_done`. Also offers revert. */
const FlagControl = ({ field, sid, onFieldUpdate }: FlagControlProps) => {
  const [busy, setBusy] = useState(false);

  const run = (fn: () => Promise<Field>, okMsg?: string) => {
    setBusy(true);
    fn()
      .then((updated) => {
        onFieldUpdate(updated);
        if (okMsg) toast.success(okMsg);
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 409) {
          toast.warn('Listen to the full working audio before marking done.');
        } else {
          toast.error(`Action failed: ${e instanceof ApiError ? e.detail : 'network error'}`);
        }
      })
      .finally(() => setBusy(false));
  };

  const setFlag = (flag: FlagValue) => run(() => api.postFlag(sid, field.fid, flag));

  const doneDisabled = busy || !field.can_mark_done;
  const badge = FLAG_BADGE[field.flag];

  return (
    <div
      id={`field-${field.fid}`}
      data-field-anchor
      data-fid={field.fid}
      data-done={field.flag === 'done' ? 'true' : 'false'}
      className="flex scroll-mt-24 flex-wrap items-center gap-2 rounded"
    >
      <span className={`rounded px-2 py-0.5 text-xs font-medium ${badge.cls}`}>{badge.label}</span>

      <button
        type="button"
        disabled={doneDisabled}
        onClick={() => setFlag('done')}
        title={field.can_mark_done ? 'Mark this field done' : 'Working audio must be fully played first'}
        className="rounded border border-custom-green px-2 py-1 text-xs text-custom-green enabled:hover:bg-custom-green enabled:hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
      >
        Mark done
      </button>

      <button
        type="button"
        disabled={busy}
        onClick={() => setFlag('edit_required')}
        className="rounded border border-amber-600 px-2 py-1 text-xs text-amber-400 enabled:hover:bg-amber-600 enabled:hover:text-white disabled:opacity-40"
      >
        Edit required
      </button>

      {field.flag !== 'none' && (
        <button
          type="button"
          disabled={busy}
          onClick={() => setFlag('none')}
          className="rounded border border-gray-600 px-2 py-1 text-xs text-gray-300 enabled:hover:bg-gray-700 disabled:opacity-40"
        >
          Clear
        </button>
      )}

      <button
        type="button"
        disabled={busy}
        onClick={() =>
          run(() => api.revert(sid, field.fid), 'Reverted to original text + audio.')
        }
        title="Change text and working audio file back to original"
        className="ml-auto text-xs text-gray-500 underline enabled:hover:text-gray-300 disabled:opacity-40"
      >
        Revert to original
      </button>
    </div>
  );
};

export default FlagControl;
