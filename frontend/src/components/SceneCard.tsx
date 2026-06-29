import { memo, useEffect, useRef, useState } from 'react';
import type { Field, Scene } from '../api';
import EditableField from './EditableField';
import AudioReview from './AudioReview';
import RegenerateControls from './RegenerateControls';
import FlagControl from './FlagControl';
import CommentBox from './CommentBox';
import AudioFieldBlock from './AudioFieldBlock';

interface SceneCardProps {
  scene: Scene;
  /** Live field state for this scene, in the same order as scene.fields. */
  fields: Field[];
  sid: string;
  onFieldUpdate: (f: Field) => void;
}

const optionIndex = (fieldPath: string): number | null => {
  const m = fieldPath.match(/^questionOption\[(\d+)\]$/);
  return m ? Number(m[1]) : null;
};

const FieldShell = ({ children }: { children: React.ReactNode }) => (
  <div className="space-y-2 rounded-md border border-gray-800 bg-gray-900/40 p-3">{children}</div>
);

/** Scene preview: the VID/PIC thumbnail JPG (served from R2), falling back to a
 * static-360 still, then a placeholder. Vimeo embeds are not used. */
const SceneMedia = ({ scene }: { scene: Scene }) => {
  const src = scene.thumb_url ?? scene.image_url;
  if (!src) {
    return (
      <div className="flex h-40 w-full items-center justify-center rounded-md border border-gray-800 bg-black/40 text-xs text-gray-600">
        no thumbnail
      </div>
    );
  }
  return (
    <div className="flex w-full items-center justify-center rounded-md border border-gray-800 bg-black">
      <img
        src={src}
        alt={`Scene ${scene.index} thumbnail`}
        loading="lazy"
        className="max-h-96 w-full object-contain"
      />
    </div>
  );
};

const SceneCard = ({ scene, fields, sid, onFieldUpdate }: SceneCardProps) => {
  const sceneDesc = fields.find((f) => f.field_path === 'SceneDesc');
  const titleKey = fields.find((f) => f.field_path === 'titleKey');
  const questionKey = fields.find((f) => f.field_path === 'questionKey');
  const options = fields.filter((f) => optionIndex(f.field_path) !== null);

  // Live SceneDesc text + textarea ref, for "Generate from edit" + highlight mode.
  const descTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const descFlushRef = useRef<(() => Promise<void>) | null>(null);
  const [descLive, setDescLive] = useState(sceneDesc?.current_text ?? '');

  // S3: keep descLive in step with external changes to the field (e.g. a revert),
  // not just keystrokes — otherwise the regenerate gates read stale text.
  const descCurrent = sceneDesc?.current_text;
  useEffect(() => {
    if (descCurrent !== undefined) setDescLive(descCurrent);
  }, [descCurrent]);

  const getSelectionRange = () => {
    const el = descTextareaRef.current;
    if (!el) return null;
    return { start: el.selectionStart, end: el.selectionEnd };
  };

  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <span className="rounded bg-gray-700 px-2 py-0.5 text-xs font-medium text-gray-200">Scene {scene.index}</span>
        {!scene.has_audio && <span className="text-xs text-gray-500">text-only (no audio)</span>}
        {scene.is_static_image && <span className="text-xs text-gray-500">360 still</span>}
      </div>

      <SceneMedia scene={scene} />

      {scene.overlays.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {scene.overlays.map((o) => (
            <figure key={o.filename} className="w-24">
              <img src={o.url} alt={o.filename} loading="lazy" className="h-16 w-24 rounded border border-gray-700 object-cover" />
              <figcaption className="mt-0.5 truncate text-[10px] text-gray-500" title={o.filename}>
                {o.filename}
              </figcaption>
            </figure>
          ))}
        </div>
      )}

      <div className="mt-4 space-y-4">
        {titleKey && (
          <FieldShell>
            <AudioFieldBlock field={titleKey} sid={sid} onFieldUpdate={onFieldUpdate} label="Title" singleLine />
          </FieldShell>
        )}

        {sceneDesc && (
          <FieldShell>
            <EditableField
              field={sceneDesc}
              sid={sid}
              onFieldUpdate={onFieldUpdate}
              onLocalChange={setDescLive}
              label="Narration (SceneDesc)"
              textareaRef={descTextareaRef}
              flushRef={descFlushRef}
              rows={4}
            />
            {sceneDesc.has_audio && (
              <>
                <AudioReview field={sceneDesc} sid={sid} onFieldUpdate={onFieldUpdate} />
                <RegenerateControls
                  field={sceneDesc}
                  sid={sid}
                  onFieldUpdate={onFieldUpdate}
                  hasTextChange={descLive !== sceneDesc.original_text}
                  getSelectionRange={getSelectionRange}
                  onBeforeRegenerate={async () => {
                    await descFlushRef.current?.();
                  }}
                />
              </>
            )}
            <FlagControl field={sceneDesc} sid={sid} onFieldUpdate={onFieldUpdate} />
            <CommentBox field={sceneDesc} sid={sid} onFieldUpdate={onFieldUpdate} />
          </FieldShell>
        )}

        {(questionKey || options.length > 0) && (
          <FieldShell>
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">Question</p>
            {questionKey && (
              <AudioFieldBlock field={questionKey} sid={sid} onFieldUpdate={onFieldUpdate} label="Prompt" rows={2} />
            )}

            {options.map((opt) => {
              const k = optionIndex(opt.field_path);
              return (
                <div key={opt.fid} className="border-t border-gray-800 pt-2">
                  <AudioFieldBlock
                    field={opt}
                    sid={sid}
                    onFieldUpdate={onFieldUpdate}
                    singleLine
                    header={
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-gray-400">Option {k}</span>
                        {k === 0 && (
                          <span className="rounded bg-custom-green px-2 py-0.5 text-[11px] font-medium text-white">
                            ✓ Correct answer (option 1)
                          </span>
                        )}
                      </div>
                    }
                  />
                </div>
              );
            })}
          </FieldShell>
        )}
      </div>
    </section>
  );
};

// Memoised: a keystroke in one scene must not re-render the other ~20 scenes.
export default memo(SceneCard);
