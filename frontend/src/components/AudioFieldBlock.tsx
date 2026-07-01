import { useRef, type ReactNode } from 'react';
import type { Field } from '../api';
import EditableField from './EditableField';
import AudioReview from './AudioReview';
import RegenerateControls from './RegenerateControls';
import FlagControl from './FlagControl';
import CommentBox from './CommentBox';

interface AudioFieldBlockProps {
  field: Field;
  sid: string;
  onFieldUpdate: (f: Field) => void;
  label?: string;
  /** Rendered above the editor (e.g. the option number + correct-answer badge). */
  header?: ReactNode;
  singleLine?: boolean;
  rows?: number;
  /** Session is locked (submitted/approving/approved) — edit controls go
   * `inert`, but the audio player stays interactive so the take can still be heard. */
  readOnly?: boolean;
}

/**
 * One editable field that may carry audio (titleKey / questionKey / option).
 * Owns its own awaitable flush so a whole-regenerate always persists the latest
 * text first (S3). SceneDesc is rendered inline in SceneCard because it has the
 * extra segment/highlight controls.
 */
const AudioFieldBlock = ({ field, sid, onFieldUpdate, label, header, singleLine, rows, readOnly = false }: AudioFieldBlockProps) => {
  const flushRef = useRef<(() => Promise<void>) | null>(null);

  return (
    <div className="space-y-2">
      {header}
      <div inert={readOnly}>
        <EditableField
          field={field}
          sid={sid}
          onFieldUpdate={onFieldUpdate}
          label={label}
          singleLine={singleLine}
          rows={rows}
          flushRef={flushRef}
        />
      </div>
      {field.has_audio && (
        <>
          <AudioReview field={field} sid={sid} onFieldUpdate={onFieldUpdate} />
          <div inert={readOnly}>
            <RegenerateControls
              field={field}
              sid={sid}
              onFieldUpdate={onFieldUpdate}
              hasTextChange={false}
              wholeOnly
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

export default AudioFieldBlock;
