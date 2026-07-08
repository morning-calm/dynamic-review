import { useEffect, useRef, useState } from 'react';
import { toast } from 'react-toastify';
import { api, ApiError, flushCommentBeacon, type Field } from '../api';
import { useDebouncedCallback } from '../hooks';
import { useSaveCoordinator } from '../saveStatusContext';

interface CommentBoxProps {
  field: Field;
  sid: string;
  onFieldUpdate: (f: Field) => void;
}

/** Autosaved reviewer comment for a field. */
const CommentBox = ({ field, sid, onFieldUpdate }: CommentBoxProps) => {
  const [value, setValue] = useState(field.comment);
  const savedRef = useRef(field.comment);
  const valueRef = useRef(value);
  valueRef.current = value;
  const { begin, end } = useSaveCoordinator();

  useEffect(() => {
    if (field.comment !== savedRef.current) {
      savedRef.current = field.comment;
      setValue(field.comment);
    }
  }, [field.comment]);

  // CO3: flush a pending comment edit on tab hide / unload (not only on blur).
  useEffect(() => {
    const flushOnHide = () => {
      const v = valueRef.current;
      if (v !== savedRef.current) {
        savedRef.current = v;
        flushCommentBeacon(sid, field.fid, v);
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

  const save = useDebouncedCallback((text: string) => {
    if (text === savedRef.current) return;
    savedRef.current = text;
    begin();
    api
      .postComment(sid, field.fid, text)
      .then((updated) => {
        end(true);
        onFieldUpdate(updated);
      })
      .catch((e: unknown) => {
        end(false);
        toast.error(`Couldn't save comment: ${e instanceof ApiError ? e.detail : 'network error'}`);
      });
  }, 1000);

  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-xs text-gray-400 hover:text-gray-200">
        Comment{value ? ' ✎' : ''}
      </summary>
      <textarea
        value={value}
        placeholder="Reviewer note (autosaved)…"
        onChange={(e) => {
          setValue(e.target.value);
          save.call(e.target.value);
        }}
        onBlur={() => save.flush()}
        rows={2}
        className="mt-1 w-full resize-y rounded border border-gray-700 bg-gray-900 px-2 py-1 text-base text-gray-200 outline-none focus:border-custom-green sm:text-xs"
      />
    </details>
  );
};

export default CommentBox;
