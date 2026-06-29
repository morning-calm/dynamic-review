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
}

/**
 * One editable field that may carry audio (titleKey / questionKey / option).
 * Owns its own awaitable flush so a whole-regenerate always persists the latest
 * text first (S3). SceneDesc is rendered inline in SceneCard because it has the
 * extra segment/highlight controls.
 */
const AudioFieldBlock = ({ field, sid, onFieldUpdate, label, header, singleLine, rows }: AudioFieldBlockProps) => {
  const flushRef = useRef<(() => Promise<void>) | null>(null);

  return (
    <div className="space-y-2">
      {header}
      <EditableField
        field={field}
        sid={sid}
        onFieldUpdate={onFieldUpdate}
        label={label}
        singleLine={singleLine}
        rows={rows}
        flushRef={flushRef}
      />
      {field.has_audio && (
        <>
          <AudioReview field={field} sid={sid} onFieldUpdate={onFieldUpdate} />
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
        </>
      )}
      <FlagControl field={field} sid={sid} onFieldUpdate={onFieldUpdate} />
      <CommentBox field={field} sid={sid} onFieldUpdate={onFieldUpdate} />
    </div>
  );
};

export default AudioFieldBlock;
