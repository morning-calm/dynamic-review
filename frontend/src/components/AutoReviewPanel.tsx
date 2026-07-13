import { useState } from 'react';
import {
  api,
  isEditableStatus,
  type Finding,
  type FindingAction,
  type FindingsPayload,
  type Session,
} from '../api';
import { findingFieldPath } from '../findings';
import { useAuth } from '../authContext';

/**
 * The reviewer answers the AI (Gate 2). Shown on the Review page whenever a session has
 * findings; while any are open the session sits in `ai_review` — back with the reviewer,
 * out of the admin's approve queue — and Submit is blocked until each one is answered.
 *
 * Three answers (dave, 2026-07-13):
 *   Resolved  — the reviewer actioned it (often via "Apply suggested fix" + a listen).
 *   Rejected  — they keep their version. A reason is REQUIRED: it's what the admin reads
 *               instead of the change.
 *   For admin — the finding is about the ENGLISH/source, which isn't the reviewer's to
 *               change. Optional note.
 *
 * Answering never edits text: actioning a suggestion goes through the normal autosave
 * path, so the reviewer always hears/sees the result before it counts as resolved.
 */

const VERDICT_STYLE: Record<Finding['verdict'], { label: string; cls: string }> = {
  warning: { label: 'Warning', cls: 'border-amber-600 bg-amber-900/20' },
  needs_human: { label: 'Needs a decision', cls: 'border-red-700 bg-red-900/20' },
};

const STATUS_STYLE: Record<Exclude<Finding['status'], 'open'>, { label: string; cls: string }> = {
  resolved: { label: 'Resolved', cls: 'bg-emerald-700' },
  rejected: { label: 'Rejected — kept my version', cls: 'bg-gray-600' },
  deferred: { label: 'For the admin (English)', cls: 'bg-blue-700' },
};

const fieldLabel = (f: Finding): string => {
  const name = findingFieldPath(f);
  return f.scene !== null ? `Scene ${f.scene} · ${name}` : name;
};

export const FindingCard = ({
  finding,
  sid,
  isZh,
  readOnly,
  showJump = true,
  onAnswered,
  onApplied,
}: {
  finding: Finding;
  sid: string;
  isZh: boolean;
  readOnly: boolean;
  /** The summary panel links out to the scene; the in-scene copy is already there. */
  showJump?: boolean;
  onAnswered: (p: FindingsPayload) => void;
  onApplied: () => void;
}) => {
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<FindingAction | 'apply' | null>(null);
  const [err, setErr] = useState('');
  const [showReject, setShowReject] = useState(false);

  const answer = async (action: FindingAction) => {
    // A rejection with no reason is refused by the backend too — surface the ask here
    // rather than letting the reviewer hit a 422.
    if (action === 'rejected' && !note.trim()) {
      setShowReject(true);
      setErr('Tell the admin why you’re keeping your version.');
      return;
    }
    setBusy(action);
    setErr('');
    try {
      onAnswered(await api.respondFinding(sid, finding.id, action, note.trim()));
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save that answer.');
    } finally {
      setBusy(null);
    }
  };

  const applyFix = async () => {
    setBusy('apply');
    setErr('');
    try {
      await api.applySuggestedFix(sid, {
        scene: finding.scene as number,
        field: finding.field,
        option: finding.option,
      });
      onApplied();   // reload the session so the edited text shows in the scene below
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not apply the fix.');
    } finally {
      setBusy(null);
    }
  };

  const open = finding.status === 'open';
  const style = VERDICT_STYLE[finding.verdict];
  // Only _ZH fixes carry a machine verification, and an unverified one is refused by the
  // backend — so the button appears only when applying it is actually allowed.
  const canApply =
    open && !readOnly && isZh && finding.scene !== null && !!finding.suggested_fix &&
    finding.suggested_fix_verified === true;

  return (
    <li className={`rounded border p-3 ${open ? style.cls : 'border-gray-700 bg-gray-800/40'}`}>
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-gray-100">{fieldLabel(finding)}</span>
        {finding.status === 'open' ? (
          <span className="rounded bg-gray-700 px-2 py-0.5 text-xs text-gray-200">{style.label}</span>
        ) : (
          <span className={`rounded px-2 py-0.5 text-xs text-white ${STATUS_STYLE[finding.status].cls}`}>
            {STATUS_STYLE[finding.status].label}
          </span>
        )}
        {showJump && finding.scene !== null && (
          <a href={`#scene-${finding.scene}`} className="ml-auto text-xs text-blue-300 hover:underline">
            Go to scene {finding.scene} →
          </a>
        )}
      </div>

      <ul className="ml-4 list-disc space-y-0.5 text-sm text-gray-300">
        {finding.reasons.map((r, i) => (
          <li key={i}>{r}</li>
        ))}
      </ul>

      {finding.suggested_fix && (
        <div className="mt-2 rounded bg-gray-900/60 p-2 text-xs">
          <div className="mb-1 flex items-center gap-2 text-gray-400">
            <span>Suggested fix</span>
            {finding.suggested_fix_verified === false && (
              <span className="rounded bg-red-800 px-1.5 py-0.5 text-red-100">
                failed machine check — don’t paste as-is
              </span>
            )}
            {finding.suggested_fix_verified === true && (
              <span className="rounded bg-emerald-800 px-1.5 py-0.5 text-emerald-100">verified</span>
            )}
          </div>
          {Object.entries(finding.suggested_fix).map(([script, text]) => (
            <div key={script} className="text-gray-200">
              <span className="text-gray-500">{script}: </span>
              {text}
            </div>
          ))}
        </div>
      )}

      {!open && finding.note && (
        <p className="mt-2 whitespace-pre-wrap rounded bg-gray-900/60 p-2 text-xs text-gray-300">
          <span className="text-gray-500">Your note to the admin: </span>
          {finding.note}
        </p>
      )}

      {open && !readOnly && (
        <>
          {showReject && (
            <textarea
              className="mt-2 w-full rounded border border-gray-600 bg-gray-900 p-2 text-sm text-gray-100"
              rows={2}
              autoFocus
              placeholder="Why are you keeping your version? (the admin reads this)"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          )}
          <div className="mt-2 flex flex-wrap gap-2">
            {canApply && (
              <button
                type="button"
                disabled={busy !== null}
                onClick={applyFix}
                className="rounded bg-gray-700 px-2 py-1 text-xs text-gray-100 hover:bg-gray-600 disabled:opacity-50"
              >
                {busy === 'apply' ? 'Applying…' : 'Apply suggested fix'}
              </button>
            )}
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => answer('resolved')}
              className="rounded bg-emerald-700 px-2 py-1 text-xs text-white hover:bg-emerald-600 disabled:opacity-50"
            >
              {busy === 'resolved' ? 'Saving…' : 'I’ve actioned this'}
            </button>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => answer('rejected')}
              className="rounded bg-gray-600 px-2 py-1 text-xs text-white hover:bg-gray-500 disabled:opacity-50"
            >
              {busy === 'rejected' ? 'Saving…' : 'Keep my version…'}
            </button>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => answer('deferred')}
              className="rounded bg-blue-700 px-2 py-1 text-xs text-white hover:bg-blue-600 disabled:opacity-50"
            >
              {busy === 'deferred' ? 'Saving…' : 'For the admin (English)'}
            </button>
          </div>
          {!showReject && (
            <p className="mt-1 text-xs text-gray-500">
              Leaving a note? Press “Keep my version…” or “For the admin” and add one there.
            </p>
          )}
          {err && <p className="mt-1 text-xs text-red-400">{err}</p>}
        </>
      )}
    </li>
  );
};

/**
 * The summary panel at the top of the Review page: every finding in one list, each linking
 * down to its scene. The findings state is owned by the PAGE (not this component) because
 * the same findings are also rendered inside each scene — answering one anywhere has to
 * update both surfaces at once.
 */
const AutoReviewPanel = ({
  session,
  payload,
  onAnswered,
  onApplied,
}: {
  session: Session;
  payload: FindingsPayload | null;
  onAnswered: (p: FindingsPayload) => void;
  onApplied: () => void;
}) => {
  const sid = session.id;
  const { user } = useAuth();
  const [skipping, setSkipping] = useState(false);

  if (!payload || payload.findings.length === 0) return null;

  const { findings, open } = payload;
  const isZh = !!session.is_zh;
  // Read-only once the trip has left the reviewer (admin took it back / approved it): the
  // findings stay visible as the record of what was answered, but nothing is actionable.
  // Any EDITABLE status is answerable (matches the backend's _EDIT gate), not just
  // 'ai_review' — open findings can survive into 'in_review' (report landed mid-approve,
  // approve reverted, reviewer recalled) and Submit is still blocked on them, so the
  // buttons must be reachable there too or the trip wedges.
  const readOnly = !isEditableStatus(session.status);
  const isAdmin = user?.role === 'admin';

  const skip = async () => {
    setSkipping(true);
    try {
      onAnswered(await api.skipFindingsTriage(sid));
      onApplied();
    } finally {
      setSkipping(false);
    }
  };

  return (
    <section id="ai-review-panel" className="scroll-mt-24 rounded-lg border border-purple-700 bg-purple-900/20 p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-purple-100">AI review</h2>
        <span className="rounded bg-purple-700 px-2 py-0.5 text-xs text-white">
          {open > 0 ? `${open} to answer` : 'all answered'}
        </span>
        {/* The backend's skip endpoint only acts on 'ai_review' (409 otherwise), so the
            button is scoped tighter than the answer buttons. */}
        {isAdmin && session.status === 'ai_review' && open > 0 && (
          <button
            type="button"
            onClick={skip}
            disabled={skipping}
            className="ml-auto rounded border border-purple-600 px-2 py-1 text-xs text-purple-200 hover:bg-purple-800/40 disabled:opacity-50"
          >
            {skipping ? 'Taking back…' : 'Take back without triage (admin)'}
          </button>
        )}
      </div>

      <p className="mb-3 text-xs text-purple-200/80">
        {readOnly
          ? 'These were the AI’s findings and how they were answered.'
          : open > 0
            ? 'An automated reviewer checked your edits for meaning, wording and Q&A logic. Click a ' +
              'finding to jump to its scene, where the same remark and buttons appear next to the text. ' +
              'Answer each one — action it, keep your version (say why), or hand it to the admin if it’s ' +
              'about the English. You can submit again once they’re all answered.'
            : 'All answered — submit when you’re ready.'}
      </p>

      {/* FindingCard's root IS the <li> — no wrapper item (it would nest li > li). */}
      <ul className="space-y-2">
        {findings.map((f) => (
          <FindingCard
            key={f.id}
            finding={f}
            sid={sid}
            isZh={isZh}
            readOnly={readOnly}
            onAnswered={onAnswered}
            onApplied={onApplied}
          />
        ))}
      </ul>
    </section>
  );
};

export default AutoReviewPanel;
