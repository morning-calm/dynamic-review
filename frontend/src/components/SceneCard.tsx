import { memo, useEffect, useRef, useState } from 'react';
import type { Field, Scene } from '../api';
import EditableField from './EditableField';
import AudioReview from './AudioReview';
import RegenerateControls from './RegenerateControls';
import FlagControl from './FlagControl';
import CommentBox from './CommentBox';
import AudioFieldBlock from './AudioFieldBlock';
import ZhFieldBlock from './ZhFieldBlock';

interface SceneCardProps {
  scene: Scene;
  /** Live field state for this scene, in the same order as scene.fields. */
  fields: Field[];
  sid: string;
  onFieldUpdate: (f: Field) => void;
  /** Session is locked (submitted/approving/approved) — edit controls go
   * `inert`, but audio players stay interactive so the take can still be heard. */
  readOnly?: boolean;
  /** `_ZH` A/B-audition mode (review-app-chinese-review.md): renders the 4-script
   * editable block + V2/V3 players instead of the splice/regenerate flow. */
  isZh?: boolean;
  /** Narration language ("English"/"Mandarin"/"Japanese"). Japanese SceneDesc voices the
   * last (kana) line and can't use the English selection ops, so those are gated off. */
  language?: string;
}

/** The line ElevenLabs actually voices for a Japanese SceneDesc: the last non-empty line
 * (the kana under the kanji). Editing only the kanji line therefore changes nothing in the
 * audio — the "Generate from edit" gate keys off this so it doesn't light up spuriously. */
const spokenLine = (text: string): string => {
  const ls = text.split('\n').map((s) => s.trim()).filter(Boolean);
  return ls.length ? ls[ls.length - 1] : text;
};

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

const SceneCard = ({ scene, fields, sid, onFieldUpdate, readOnly = false, isZh = false, language }: SceneCardProps) => {
  const isJp = language === 'Japanese';
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
            {isZh ? (
              <ZhFieldBlock field={titleKey} sid={sid} onFieldUpdate={onFieldUpdate} label="Title" rows={2} readOnly={readOnly} />
            ) : (
              <AudioFieldBlock field={titleKey} sid={sid} onFieldUpdate={onFieldUpdate} label="Title" rows={2} readOnly={readOnly} />
            )}
          </FieldShell>
        )}

        {sceneDesc && (
          <FieldShell>
            {isZh ? (
              <ZhFieldBlock
                field={sceneDesc}
                sid={sid}
                onFieldUpdate={onFieldUpdate}
                label="Narration (SceneDesc)"
                rows={4}
                readOnly={readOnly}
              />
            ) : (
              <>
                <div inert={readOnly}>
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
                </div>
                {isJp && (
                  <p className="text-xs text-gray-500">
                    Audio is voiced from the <span className="text-gray-300">last line (kana)</span>. Edit
                    that line to change the narration — editing only the kanji won’t alter the audio. The
                    highlight/cursor tools below also work on the kana line.
                  </p>
                )}
                {sceneDesc.has_audio && (
                  <>
                    <AudioReview field={sceneDesc} sid={sid} onFieldUpdate={onFieldUpdate} />
                    <div inert={readOnly}>
                      <RegenerateControls
                        field={sceneDesc}
                        sid={sid}
                        onFieldUpdate={onFieldUpdate}
                        // JP: only a change to the voiced (kana) line should enable "Generate
                        // from edit" — compared against what the WORKING take says (set at
                        // combine), not the seed, or the button re-lights after a combine when
                        // only the kanji was touched. English keys off the whole field.
                        hasTextChange={
                          isJp
                            ? spokenLine(descLive) !==
                              spokenLine(sceneDesc.working_text ?? sceneDesc.original_text)
                            : descLive !== sceneDesc.original_text
                        }
                        getSelectionRange={getSelectionRange}
                        // The selection ops work for JP too now (the CJK backend maps the kana
                        // selection via the MMS aligner; a kanji-line selection gets a 409 hint).
                        surfaceLabel={isJp ? 'the narration kana line' : 'the narration'}
                        onBeforeRegenerate={async () => {
                          await descFlushRef.current?.();
                        }}
                      />
                    </div>
                  </>
                )}
                <div className="space-y-2" inert={readOnly}>
                  <FlagControl field={sceneDesc} sid={sid} onFieldUpdate={onFieldUpdate} />
                  <CommentBox field={sceneDesc} sid={sid} onFieldUpdate={onFieldUpdate} />
                </div>
              </>
            )}
          </FieldShell>
        )}

        {(questionKey || options.length > 0) && (
          <FieldShell>
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">Question</p>
            {questionKey && (
              isZh ? (
                <ZhFieldBlock field={questionKey} sid={sid} onFieldUpdate={onFieldUpdate} label="Prompt" rows={2} readOnly={readOnly} />
              ) : (
                <AudioFieldBlock field={questionKey} sid={sid} onFieldUpdate={onFieldUpdate} label="Prompt" rows={2} readOnly={readOnly} />
              )
            )}

            {options.map((opt) => {
              const k = optionIndex(opt.field_path);
              const header = (
                <div className="flex items-center justify-between">
                  <span className="text-xs text-gray-400">Option {k}</span>
                  {k === 0 && (
                    <span className="rounded bg-custom-green px-2 py-0.5 text-[11px] font-medium text-white">
                      ✓ Correct answer (option 1)
                    </span>
                  )}
                </div>
              );
              return (
                <div key={opt.fid} className="border-t border-gray-800 pt-2">
                  {isZh ? (
                    <ZhFieldBlock field={opt} sid={sid} onFieldUpdate={onFieldUpdate} rows={3} readOnly={readOnly} header={header} />
                  ) : (
                    <AudioFieldBlock field={opt} sid={sid} onFieldUpdate={onFieldUpdate} rows={3} readOnly={readOnly} header={header} />
                  )}
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
