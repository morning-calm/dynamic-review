import type { ReactNode } from 'react';
import type { Field } from '../api';
import EditableField from './EditableField';
import LocalizationEditor from './LocalizationEditor';
import ZhAudioAB from './ZhAudioAB';
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
const ZhFieldBlock = ({ field, sid, onFieldUpdate, label, header, singleLine, rows, readOnly = false }: ZhFieldBlockProps) => (
  <div className="space-y-2">
    {header}
    <div inert={readOnly}>
      {field.localization ? (
        <LocalizationEditor field={field} sid={sid} onFieldUpdate={onFieldUpdate} label={label} rows={rows} />
      ) : (
        <EditableField field={field} sid={sid} onFieldUpdate={onFieldUpdate} label={label} singleLine={singleLine} rows={rows} />
      )}
    </div>
    {field.has_audio && <ZhAudioAB v2={field.audio.v2} v3={field.audio.v3} />}
    <div className="space-y-2" inert={readOnly}>
      <FlagControl field={field} sid={sid} onFieldUpdate={onFieldUpdate} />
      <CommentBox field={field} sid={sid} onFieldUpdate={onFieldUpdate} />
    </div>
  </div>
);

export default ZhFieldBlock;
