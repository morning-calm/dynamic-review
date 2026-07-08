import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { toast } from 'react-toastify';
import { api, ApiError, flushFieldBeacon, type Field } from '../api';
import { useDebouncedCallback } from '../hooks';
import { useSaveCoordinator } from '../saveStatusContext';
import InlineDiff from './InlineDiff';
import SourceEditor from './SourceEditor';

interface EditableFieldProps {
  field: Field;
  sid: string;
  onFieldUpdate: (f: Field) => void;
  /** Live local text (fires on every keystroke) — lets the parent gate "Generate from edit". */
  onLocalChange?: (text: string) => void;
  label?: string;
  placeholder?: string;
  /** Single-line style (titleKey / contentTitleKey). */
  singleLine?: boolean;
  /** Attach the underlying textarea (so the parent can read selectionStart/End). */
  textareaRef?: RefObject<HTMLTextAreaElement | null>;
  rows?: number;
  /**
   * The parent sets this to a function that flushes any pending save and resolves
   * once the PUT completes — call it before a segment/highlight regenerate so the
   * server diffs the intended (saved) text (S3).
   */
  flushRef?: RefObject<(() => Promise<void>) | null>;
}

const RETRY_MS = 3000;

/**
 * Textarea bound to `current_text`. Autosaves on ~1 s idle and on blur, shows an
 * inline (debounced) diff once the text diverges from the original, and exposes
 * its textarea + a flush handle so the parent can regenerate against saved text.
 */
const EditableField = ({
  field,
  sid,
  onFieldUpdate,
  onLocalChange,
  label,
  placeholder,
  singleLine = false,
  textareaRef,
  rows,
  flushRef,
}: EditableFieldProps) => {
  const [value, setValue] = useState(field.current_text);
  const savedRef = useRef(field.current_text);
  const valueRef = useRef(value); // CO1: read latest value without re-binding listeners
  valueRef.current = value;
  const { begin, end } = useSaveCoordinator();

  // Adopt external text changes (e.g. revert) without clobbering active typing.
  useEffect(() => {
    if (field.current_text !== savedRef.current) {
      savedRef.current = field.current_text;
      setValue(field.current_text);
    }
  }, [field.current_text]);

  // CO2: feed the diff a value debounced ~300 ms behind the live text.
  const [diffValue, setDiffValue] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDiffValue(value), 300);
    return () => clearTimeout(id);
  }, [value]);

  // Single writer. Tracks the in-flight PUT so an awaiting flush waits for the
  // ACTUAL request (savedRef is set optimistically, so it can't be the signal).
  // S2: on failure, roll savedRef back so the unsaved delta is seen again (unload
  // beacon AND next debounce) and schedule ONE real retry.
  const inFlightRef = useRef<Promise<void> | null>(null);
  const persistRef = useRef<(text: string, isRetry?: boolean) => Promise<void>>(() => Promise.resolve());
  const persist = useCallback(
    async (text: string, isRetry = false): Promise<void> => {
      if (text === savedRef.current) {
        // Nothing new to send, but wait for any in-flight save to land.
        if (inFlightRef.current) await inFlightRef.current;
        return;
      }
      const prev = savedRef.current;
      savedRef.current = text;
      begin();
      const req = (async () => {
        try {
          const updated = await api.putField(sid, field.fid, text);
          end(true);
          onFieldUpdate(updated);
        } catch (e: unknown) {
          end(false);
          savedRef.current = prev; // delta is unsaved again
          toast.error(`Couldn't save ${field.field_path}: ${e instanceof ApiError ? e.detail : 'network error'}`);
          if (!isRetry) window.setTimeout(() => void persistRef.current(text, true), RETRY_MS);
        }
      })();
      inFlightRef.current = req;
      try {
        await req;
      } finally {
        if (inFlightRef.current === req) inFlightRef.current = null;
      }
    },
    [sid, field.fid, field.field_path, begin, end, onFieldUpdate],
  );
  persistRef.current = persist;

  const save = useDebouncedCallback((text: string) => void persist(text), 1000);

  // Flush a pending save when the tab is hidden or unloaded (CO1: value via ref).
  useEffect(() => {
    const flushOnHide = () => {
      const v = valueRef.current;
      if (v !== savedRef.current) {
        savedRef.current = v;
        flushFieldBeacon(sid, field.fid, v);
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') flushOnHide();
    };
    window.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('beforeunload', flushOnHide);
    return () => {
      window.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('beforeunload', flushOnHide);
    };
  }, [sid, field.fid]);

  // S3: expose an awaitable flush so the parent can persist (and await) the latest
  // text before regenerating, so the server diffs the intended text.
  useEffect(() => {
    if (!flushRef) return;
    const ref = flushRef;
    ref.current = async () => {
      save.cancel();
      await persist(valueRef.current);
    };
    return () => {
      ref.current = null;
    };
  }, [flushRef, save, persist]);

  const handleChange = (text: string) => {
    setValue(text);
    onLocalChange?.(text);
    save.call(text);
  };

  const changed = value !== field.original_text;

  const baseClasses =
    'w-full resize-y rounded border bg-gray-900 px-3 py-2 text-base sm:text-sm text-gray-100 outline-none focus:border-custom-green ' +
    (changed ? 'border-amber-600/60' : 'border-gray-700');

  return (
    <div>
      {label && <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-400">{label}</label>}
      <textarea
        ref={textareaRef}
        value={value}
        placeholder={placeholder}
        onChange={(e) => handleChange(e.target.value)}
        onBlur={() => save.flush()}
        rows={rows ?? (singleLine ? 1 : 3)}
        spellCheck
        className={baseClasses}
      />
      {changed && <InlineDiff original={field.original_text} current={diffValue} />}
      {field.source_text !== '' && <SourceEditor field={field} sid={sid} onFieldUpdate={onFieldUpdate} />}
    </div>
  );
};

export default EditableField;
