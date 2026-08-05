import { useEffect, useRef, useState } from 'react';
import { toast } from 'react-toastify';
import { api, ApiError, type Field } from '../api';
import { useDebouncedCallback } from '../hooks';
import InlineDiff from './InlineDiff';

/** Editable English translation (the *En sibling) shown UNDER the target text on a
 * non-_EN trip. Text-only — no audio. Autosaves on ~1s idle and on blur. */
const SourceEditor = ({
  field,
  sid,
  onFieldUpdate,
}: {
  field: Field;
  sid: string;
  onFieldUpdate: (f: Field) => void;
}) => {
  const [value, setValue] = useState(field.source_text);
  const savedRef = useRef(field.source_text);

  // Debounced value for the original→new diff (mirrors the target editor).
  const [diffValue, setDiffValue] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDiffValue(value), 300);
    return () => clearTimeout(id);
  }, [value]);
  const changed = value !== field.original_source;

  const save = useDebouncedCallback((text: string) => {
    if (text === savedRef.current) return;
    savedRef.current = text;
    api
      .putSource(sid, field.fid, text)
      .then(onFieldUpdate)
      .catch((e: unknown) =>
        toast.error(`Couldn't save English: ${e instanceof ApiError ? e.detail : 'network error'}`),
      );
  }, 1000);

  // Adopt external changes (e.g. revert) without clobbering active typing — and cancel
  // any pending debounced save, so a stale autosave can't re-write the reverted-away
  // English a second after the revert (same class as the EditableField fix).
  useEffect(() => {
    if (field.source_text !== savedRef.current) {
      save.cancel();
      savedRef.current = field.source_text;
      setValue(field.source_text);
    }
  }, [field.source_text, save]);

  return (
    <div className="mt-1.5">
      <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-gray-500">
        English translation
      </label>
      <textarea
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          save.call(e.target.value);
        }}
        onBlur={() => save.flush()}
        rows={2}
        spellCheck
        className={`w-full resize-y rounded border bg-gray-900 px-3 py-2 text-base text-gray-300 outline-none focus:border-custom-green sm:text-sm ${
          changed ? 'border-amber-600/60' : 'border-gray-700'
        }`}
      />
      {changed && <InlineDiff original={field.original_source} current={diffValue} />}
    </div>
  );
};

export default SourceEditor;
