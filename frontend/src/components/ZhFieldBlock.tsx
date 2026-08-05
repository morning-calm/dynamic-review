import { useRef, type ReactNode } from 'react';
import type { Field } from '../api';
import { useTextSelection } from '../hooks';
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
  // (highlight / alt / trim-noise / pause) read the reviewer's highlight/caret from it.
  // The hook persists the capture across blur (iOS collapses the selection when a tool
  // button is tapped) and invalidates it if the Hans text or the working take changes.
  const {
    ref: hansTextareaRef,
    bind: hansSelectionBind,
    getSelectionRange,
    selection,
    clearSelection,
  } = useTextSelection(field.localization?.cur.Hans ?? '', field.audio.working);
  // SceneDesc gets the highlight/selection audio tools (the backend maps the Hans
  // selection onto the spoken hanzi via the CJK aligner). Q&A fields stay whole-only.
  const isSceneDesc = field.field_path === 'SceneDesc';
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
            hansSelectionBind={hansSelectionBind}
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
              wholeOnly={!isSceneDesc}
              hasSelection={isSceneDesc && Boolean(field.localization)}
              // Only offer the selection-reading tools when a Hans surface exists —
              // a non-localized field would route char offsets into the wrong text.
              getSelectionRange={field.localization ? getSelectionRange : undefined}
              capturedSelection={field.localization ? selection : undefined}
              onClearSelection={field.localization ? clearSelection : undefined}
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
        <FlagControl
          field={field}
          sid={sid}
          onFieldUpdate={onFieldUpdate}
          beforeRevert={async () => {
            await flushRef.current?.();
          }}
        />
        <CommentBox field={field} sid={sid} onFieldUpdate={onFieldUpdate} />
      </div>
    </div>
  );
};

export default ZhFieldBlock;
