import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { toast } from 'react-toastify';
import { api, ApiError, flushLocalizationBeacon, type Field, type ZhScript } from '../api';
import { useDebouncedCallback, type SelectionBind } from '../hooks';
import { useSaveCoordinator } from '../saveStatusContext';
import InlineDiff from './InlineDiff';

const RETRY_MS = 3000;

const SCRIPT_ORDER: ZhScript[] = ['Hant', 'Hans', 'zhuyin', 'en'];
const SCRIPT_LABEL: Record<ZhScript, string> = {
  Hant: 'Traditional (Hant)',
  Hans: 'Simplified (Hans)',
  zhuyin: 'Zhuyin',
  en: 'English',
};

interface ScriptRowProps {
  sid: string;
  fid: number;
  script: ZhScript;
  cur: string;
  orig: string;
  rows: number;
  onFieldUpdate: (f: Field) => void;
  /** Register an awaitable flush (pending save → resolved PUT) with the parent, so a
   * regenerate can persist the latest hanzi before the server reads it. */
  registerFlush?: (script: ZhScript, fn: (() => Promise<void>) | null) => void;
  /** Exposes this row's textarea (the Hans row only) so the audio selection tools can
   * read the reviewer's highlight/caret from the voiced script. */
  textareaRef?: RefObject<HTMLTextAreaElement | null>;
  /** Selection-capture handlers (useTextSelection.bind), Hans row only — persists the
   * highlight/caret for the audio tools; iOS collapses it on blur otherwise. */
  selectionBind?: SelectionBind;
}

/**
 * One script's autosaved textarea. Mirrors EditableField's persist pattern
 * (debounce ~1s + blur flush; S2: rollback the saved-marker + one retry on
 * failure; flush on tab-hide/unload via a keepalive beacon) but scoped to a
 * single `{script, text}` PUT against the localization endpoint, so the 4
 * scripts on a field save independently of one another.
 */
const ScriptRow = ({ sid, fid, script, cur, orig, rows, onFieldUpdate, registerFlush, textareaRef, selectionBind }: ScriptRowProps) => {
  const [value, setValue] = useState(cur);
  const savedRef = useRef(cur);
  const valueRef = useRef(value); // read latest value without re-binding the hide/unload listener
  valueRef.current = value;
  const { begin, end } = useSaveCoordinator();

  // Adopt external changes (e.g. another reviewer, or a resume) without clobbering active typing.
  useEffect(() => {
    if (cur !== savedRef.current) {
      savedRef.current = cur;
      setValue(cur);
    }
  }, [cur]);

  // Debounced value for the orig→new diff (mirrors the target editor).
  const [diffValue, setDiffValue] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDiffValue(value), 300);
    return () => clearTimeout(id);
  }, [value]);

  const inFlightRef = useRef<Promise<void> | null>(null);
  const persistRef = useRef<(text: string, isRetry?: boolean) => Promise<void>>(() => Promise.resolve());
  const persist = useCallback(
    async (text: string, isRetry = false): Promise<void> => {
      if (text === savedRef.current) {
        if (inFlightRef.current) await inFlightRef.current;
        return;
      }
      const prev = savedRef.current;
      savedRef.current = text;
      begin();
      const req = (async () => {
        try {
          const updated = await api.putLocalization(sid, fid, script, text);
          end(true);
          onFieldUpdate(updated);
        } catch (e: unknown) {
          end(false);
          savedRef.current = prev; // delta is unsaved again
          toast.error(`Couldn't save ${SCRIPT_LABEL[script]}: ${e instanceof ApiError ? e.detail : 'network error'}`);
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
    [sid, fid, script, begin, end, onFieldUpdate],
  );
  persistRef.current = persist;

  const save = useDebouncedCallback((text: string) => void persist(text), 1000);

  // Expose an awaitable flush to the parent (S3): run any pending debounce now and wait
  // for the in-flight PUT, so a regenerate reads the just-saved hanzi.
  useEffect(() => {
    if (!registerFlush) return;
    registerFlush(script, async () => {
      save.flush();
      if (inFlightRef.current) await inFlightRef.current;
    });
    return () => registerFlush(script, null);
  }, [registerFlush, script, save]);

  // Flush a pending save when the tab is hidden or unloaded.
  useEffect(() => {
    const flushOnHide = () => {
      const v = valueRef.current;
      if (v !== savedRef.current) {
        savedRef.current = v;
        flushLocalizationBeacon(sid, fid, script, v);
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
  }, [sid, fid, script]);

  const changed = value !== orig;

  return (
    <div>
      <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-gray-500">
        {SCRIPT_LABEL[script]}
      </label>
      <textarea
        ref={textareaRef}
        {...selectionBind}
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          save.call(e.target.value);
        }}
        onBlur={() => save.flush()}
        rows={rows}
        spellCheck={script === 'en'}
        className={`w-full resize-y rounded border bg-gray-900 px-3 py-2 text-base text-gray-100 outline-none focus:border-custom-green sm:text-sm ${
          changed ? 'border-amber-600/60' : 'border-gray-700'
        }`}
      />
      {changed && <InlineDiff original={orig} current={diffValue} />}
    </div>
  );
};

interface LocalizationEditorProps {
  field: Field;
  sid: string;
  onFieldUpdate: (f: Field) => void;
  label?: string;
  rows?: number;
  /** Parent-owned handle: flush + await every script's pending save (before regenerate). */
  flushRef?: RefObject<(() => Promise<void>) | null>;
  /** Parent-owned ref to the Simplified (Hans) textarea — the VOICED script — so the
   * audio selection tools (highlight/alt/trim/pause) can read the reviewer's selection. */
  hansTextareaRef?: RefObject<HTMLTextAreaElement | null>;
  /** Selection-capture handlers for the Hans textarea (see ScriptRow.selectionBind). */
  hansSelectionBind?: SelectionBind;
}

/**
 * Stacked, editable Traditional / Simplified / Zhuyin / English block for a
 * `_ZH` field — replaces the single-text editor for fields seeded from
 * TripLocalizations. Each script autosaves independently via `PUT
 * .../localization {script,text}`; the seeded `orig` snapshot renders an
 * inline diff once a script diverges, matching the target-language editor.
 * NO pinyin — it's regenerated server-side from the confirmed Zhuyin on
 * approve. Renders nothing if the field has no localization data (the caller
 * should fall back to the plain editor in that case).
 */
const LocalizationEditor = ({ field, sid, onFieldUpdate, label, rows = 3, flushRef, hansTextareaRef, hansSelectionBind }: LocalizationEditorProps) => {
  const loc = field.localization;
  const flushers = useRef<Map<ZhScript, () => Promise<void>>>(new Map());
  const registerFlush = useCallback((script: ZhScript, fn: (() => Promise<void>) | null) => {
    if (fn) flushers.current.set(script, fn);
    else flushers.current.delete(script);
  }, []);
  useEffect(() => {
    if (!flushRef) return;
    flushRef.current = async () => {
      await Promise.all([...flushers.current.values()].map((f) => f()));
    };
    return () => {
      flushRef.current = null;
    };
  }, [flushRef]);
  if (!loc) return null;
  // Soft sibling reminder (never blocks): the four scripts are ONE fact in four forms —
  // when some changed and others didn't, the unchanged ones are probably stale
  // (2026-07-08: a whole trip shipped with Hans edited but Hant/zhuyin/en untouched).
  const present = SCRIPT_ORDER.filter((s) => loc.cur[s] != null);
  const changedScripts = present.filter((s) => (loc.cur[s] ?? '') !== (loc.orig[s] ?? ''));
  const unchangedScripts = present.filter((s) => !changedScripts.includes(s));
  const partial = changedScripts.length > 0 && unchangedScripts.length > 0;
  return (
    <div className="space-y-2">
      {label && <p className="text-xs font-medium uppercase tracking-wide text-gray-400">{label}</p>}
      {partial && (
        <p className="text-xs text-amber-400/90">
          ⚠ {changedScripts.map((s) => SCRIPT_LABEL[s]).join(' + ')} changed, but{' '}
          {unchangedScripts.map((s) => SCRIPT_LABEL[s]).join(', ')} unchanged — if the meaning changed, update those too (all four are final).
        </p>
      )}
      <div className="space-y-3 rounded border border-gray-800 bg-gray-950/40 p-2">
        {SCRIPT_ORDER.filter((s) => loc.cur[s] != null).map((s) => (
          <ScriptRow
            key={s}
            sid={sid}
            fid={field.fid}
            script={s}
            cur={loc.cur[s] ?? ''}
            orig={loc.orig[s] ?? ''}
            rows={rows}
            onFieldUpdate={onFieldUpdate}
            registerFlush={registerFlush}
            textareaRef={s === 'Hans' ? hansTextareaRef : undefined}
            selectionBind={s === 'Hans' ? hansSelectionBind : undefined}
          />
        ))}
      </div>
    </div>
  );
};

export default LocalizationEditor;
