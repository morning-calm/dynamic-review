import { useRef, type ReactNode } from 'react';
import type { Field } from '../api';
import EditableField from './EditableField';
import LocalizationEditor from './LocalizationEditor';
import AudioReview from './AudioReview';
import RegenerateControls from './RegenerateControls';
import FlagControl from './FlagControl';
import CommentBox from './CommentBox';

interface ZhFieldBlockProps {
  field: Field;
  sid: string;
  onFieldUpdate: (f: Field) => void;
  label?: string;
  /** Rendered above the editor (e.g. the option number + correct-answer badge). */
  header?: ReactNode;
  singleLine?: boolean;
  rows?: number;
  /** Session is locked (submitted/approving/approved) — edit controls go
   * `inert`, but the audio players stay interactive so the take can still be heard. */
  readOnly?: boolean;
}

/**
 * `_ZH` counterpart to AudioFieldBlock (review-app-chinese-review.md Parts 2 &
 * 3): the 4-script (Traditional/Simplified/Zhuyin/English) editable block when
 * the field carries TripLocalizations data, else the plain single-text editor
 * as a fallback (e.g. `contentTitleKey`, which isn't in TripLocalizations) —
 * plus the V2/V3 audition (no splice/regenerate/coverage UI) and the same
 * flag/comment controls every other language uses. One component covers both
 * trip-level fields (ReviewPage's header) and scene fields (SceneCard).
 */
const ZhFieldBlock = ({ field, sid, onFieldUpdate, label, header, singleLine, rows, readOnly = false }: ZhFieldBlockProps) => {
  const flushRef = useRef<(() => Promise<void>) | null>(null);
  // The Simplified (Hans) textarea — the VOICED script. The audio selection tools
  // (highlight / alt / trim-noise / pause) read the reviewer's highlight/caret from it;
  // a textarea keeps its selection after blur, so clicking a button below still sees it.
  const hansTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const getSelectionRange = () => {
    const el = hansTextareaRef.current;
    if (!el) return null;
    return { start: el.selectionStart, end: el.selectionEnd };
  };
  // SceneDesc supports the surgical CJK splice ("Generate from edit", mode=segment): the
  // backend re-voices just the edited hanzi clause and falls back to whole-regen when
  // uncertain. Enabled once the Simplified hanzi differs from the seed. Q&A fields stay
  // whole-only. The selection ops read the Hans highlight (offsets are sent as-is; the
  // backend maps them onto the spoken hanzi via the CJK aligner).
  const isSceneDesc = field.field_path === 'SceneDesc';
  // Enable "Generate from edit" only when the hanzi differs from what the WORKING take
  // currently says (working_hans, re-baselined at each combine) — not the seed. Otherwise
  // the button stays lit after a combine with nothing new to generate.
  const hanziChanged = Boolean(
    field.localization &&
      field.localization.cur.Hans !== (field.localization.working_hans ?? field.localization.orig.Hans),
  );
  return (
    <div className="space-y-2">
      {header}
      <div inert={readOnly}>
        {field.localization ? (
          <LocalizationEditor
            field={field}
            sid={sid}
            onFieldUpdate={onFieldUpdate}
            label={label}
            rows={rows}
            flushRef={flushRef}
            hansTextareaRef={hansTextareaRef}
          />
        ) : (
          <EditableField field={field} sid={sid} onFieldUpdate={onFieldUpdate} label={label} singleLine={singleLine} rows={rows} flushRef={flushRef} />
        )}
      </div>
      {field.has_audio && field.localization && isSceneDesc && (
        <p className="text-xs text-gray-500">
          Audio is voiced from the <span className="text-gray-300">Simplified (Hans)</span> script —
          edit Hans to change the narration. The highlight/cursor tools below read your selection in
          the Hans field.
        </p>
      )}
      {field.has_audio && (
        <>
          <AudioReview field={field} sid={sid} onFieldUpdate={onFieldUpdate} />
          <div inert={readOnly}>
            <RegenerateControls
              field={field}
              sid={sid}
              onFieldUpdate={onFieldUpdate}
              hasTextChange={isSceneDesc && hanziChanged}
              wholeOnly={!isSceneDesc}
              hasSelection={isSceneDesc && Boolean(field.localization)}
              // Only offer the selection-reading tools when a Hans surface exists —
              // a non-localized field would route char offsets into the wrong text.
              getSelectionRange={field.localization ? getSelectionRange : undefined}
              selectionSourceText={field.localization?.cur.Hans ?? undefined}
              surfaceLabel="the Simplified (Hans) field"
              onBeforeRegenerate={async () => {
                await flushRef.current?.();
              }}
            />
          </div>
        </>
      )}
      <div className="space-y-2" inert={readOnly}>
        <FlagControl field={field} sid={sid} onFieldUpdate={onFieldUpdate} />
        <CommentBox field={field} sid={sid} onFieldUpdate={onFieldUpdate} />
      </div>
    </div>
  );
};

export default ZhFieldBlock;
