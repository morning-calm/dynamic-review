import { useState } from 'react';
import type { Field, Session } from '../api';
import { fieldChanged } from '../fieldDiff';
import { useSaveCoordinator } from '../saveStatusContext';
import SaveStatus from './SaveStatus';
import SceneCard from './SceneCard';
import EditableField from './EditableField';
import FlagControl from './FlagControl';
import ZhFieldBlock from './ZhFieldBlock';

/**
 * Admin inline editing on the approve page: expand the trip header or a scene to get
 * the FULL reviewer toolbox (text edit, regenerate/splice, highlight, trim, pauses,
 * flags, comments) via the same SceneCard the review page uses. Only rendered while
 * the session is `submitted` and the user is an admin — the backend edit gate
 * (assert_editable) allows exactly that combination. The session stays `submitted`;
 * Approve/Send back remain available in the nav. Must be rendered inside a
 * SaveStatusProvider (it reads the autosave coordinator).
 */
const AdminInlineEdit = ({ session, onUpdate }: { session: Session; onUpdate: (f: Field) => void }) => {
  const { state: saveState } = useSaveCoordinator();
  // -1 = the trip-header pseudo-scene.
  const [open, setOpen] = useState<Set<number>>(new Set());
  const toggle = (k: number) =>
    setOpen((prev) => {
      const n = new Set(prev);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });
  const isZh = session.is_zh;
  const contentTitleKey = session.trip_fields.find((f) => f.field_path === 'contentTitleKey');
  const tripDescription = session.trip_fields.find((f) => f.field_path === 'tripgroup_description');
  const tripChanged = session.trip_fields.some(fieldChanged);

  const rowBtn =
    'flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-700/50';
  const changedChip = (
    <span className="rounded bg-amber-600/80 px-1.5 py-0.5 text-[10px] font-medium text-white">changed</span>
  );

  return (
    <section className="rounded-lg border border-purple-800/60 bg-gray-800/60 p-4">
      <div className="mb-1 flex flex-wrap items-center gap-3">
        <h2 className="text-sm font-semibold text-white">Edit inline (admin)</h2>
        <SaveStatus state={saveState} />
      </div>
      <p className="mb-3 text-xs text-gray-400">
        Final touch-ups without sending the trip back — expand a scene for the full reviewer toolbox.
        Edits autosave; the session stays <span className="text-blue-300">submitted</span>, so Approve /
        Send back above when you’re done.
      </p>

      {(contentTitleKey || tripDescription) && (
        <div className="mb-2 overflow-hidden rounded border border-gray-700">
          <button type="button" className={rowBtn} onClick={() => toggle(-1)}>
            <span className="flex items-center gap-2">
              Trip header (title &amp; description)
              {tripChanged && changedChip}
            </span>
            <span className="text-xs text-gray-500">{open.has(-1) ? 'Collapse' : 'Edit'}</span>
          </button>
          {open.has(-1) && (
            <div className="space-y-4 border-t border-gray-700 p-3">
              {contentTitleKey &&
                (isZh ? (
                  <ZhFieldBlock
                    field={contentTitleKey}
                    sid={session.id}
                    onFieldUpdate={onUpdate}
                    label="Display title (contentTitleKey)"
                    singleLine
                  />
                ) : (
                  <div className="space-y-2">
                    <EditableField
                      field={contentTitleKey}
                      sid={session.id}
                      onFieldUpdate={onUpdate}
                      label="Display title (contentTitleKey)"
                      singleLine
                    />
                    <FlagControl field={contentTitleKey} sid={session.id} onFieldUpdate={onUpdate} />
                  </div>
                ))}
              {tripDescription &&
                (isZh ? (
                  <ZhFieldBlock
                    field={tripDescription}
                    sid={session.id}
                    onFieldUpdate={onUpdate}
                    label="TripGroup description"
                    rows={4}
                  />
                ) : (
                  <div className="space-y-2">
                    <EditableField
                      field={tripDescription}
                      sid={session.id}
                      onFieldUpdate={onUpdate}
                      label="TripGroup description"
                      rows={4}
                    />
                    <FlagControl field={tripDescription} sid={session.id} onFieldUpdate={onUpdate} />
                  </div>
                ))}
            </div>
          )}
        </div>
      )}

      {session.scenes.map((scene) => {
        const sceneChanged = scene.fields.some(fieldChanged);
        const editRequired = scene.fields.some((f) => f.flag === 'edit_required');
        return (
          <div key={scene.index} className="mb-2 overflow-hidden rounded border border-gray-700">
            <button type="button" className={rowBtn} onClick={() => toggle(scene.index)}>
              <span className="flex items-center gap-2">
                Scene {scene.index}
                {sceneChanged && changedChip}
                {editRequired && (
                  <span className="rounded bg-amber-600 px-1.5 py-0.5 text-[10px] font-medium text-white">
                    edit required
                  </span>
                )}
              </span>
              <span className="text-xs text-gray-500">{open.has(scene.index) ? 'Collapse' : 'Edit'}</span>
            </button>
            {open.has(scene.index) && (
              <div className="border-t border-gray-700 p-3">
                <SceneCard
                  scene={scene}
                  fields={scene.fields}
                  sid={session.id}
                  tripId={session.trip_id}
                  onFieldUpdate={onUpdate}
                  readOnly={false}
                  isZh={isZh}
                  language={session.language}
                />
              </div>
            )}
          </div>
        );
      })}
    </section>
  );
};

export default AdminInlineEdit;
