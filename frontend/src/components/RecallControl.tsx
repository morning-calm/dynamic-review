import { useCallback, useEffect, useRef, useState } from 'react';
import Modal from 'react-modal';
import { toast } from 'react-toastify';
import { api, ApiError, type RecallState, type Session } from '../api';
import { useAuth } from '../authContext';
import { MODAL_STYLE } from '../modalStyle';

/** How often to re-poll the recall state while it matters — the admin can grant or
 * decline at any moment, and the requester should see the outcome without a reload. */
const POLL_MS = 30_000;

interface RecallControlProps {
  session: Session;
  /** Called after an auto-granted recall — the caller should re-fetch the session
   * (it just became editable again). */
  onChanged: () => void;
}

/**
 * "Recall submission" — the reviewer takes a submitted trip back.
 * Auto-grants when it's their submission and no admin is live on it; otherwise
 * collects a reason and files a pinned request for the admin queue. Also renders
 * the waiting/declined banners for the latest request.
 */
const RecallControl = ({ session, onChanged }: RecallControlProps) => {
  const { user } = useAuth();
  const [state, setState] = useState<RecallState | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);

  const status = session.status;
  const relevant = status === 'submitted' || status === 'approving' || status === 'approved';

  // Keep the latest onChanged without making it a poll dependency.
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;

  const refresh = useCallback(() => {
    if (!relevant) return;
    api
      .recallState(session.id)
      .then((s) => {
        setState(s);
        // The server-side status moved under us (e.g. the admin granted the recall →
        // changes_requested, or approved it) — have the page re-fetch the session.
        if (s.status !== status) onChangedRef.current();
      })
      .catch(() => {});
  }, [session.id, relevant, status]);

  useEffect(() => {
    refresh();
    if (!relevant) return;
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [refresh, relevant]);

  if (!user || !relevant || !state) return null;

  const doRecall = (r: string) => {
    setBusy(true);
    api
      .recall(session.id, r)
      .then((res) => {
        if (res.recalled) {
          toast.success('Submission recalled — you can edit again.');
          setModalOpen(false);
          onChanged();
        } else {
          toast.success(
            res.existing
              ? 'A recall request is already waiting for the admin.'
              : 'Recall request sent — the admin will see it pinned in their queue.',
          );
          setModalOpen(false);
          setReason('');
          refresh();
        }
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.code === 'reason_required') {
          // State moved under us (e.g. an admin opened it) — collect the reason.
          setModalOpen(true);
        } else if (e instanceof ApiError && e.code === 'bad_state') {
          toast.info('The session state changed — refreshing.');
          onChanged();
        } else {
          toast.error(`Recall failed: ${e instanceof ApiError ? e.detail : 'network error'}`);
        }
      })
      .finally(() => setBusy(false));
  };

  const openReq = state.request?.status === 'open' ? state.request : null;
  const declinedReq = state.request?.status === 'declined' ? state.request : null;

  return (
    <div className="space-y-2">
      {openReq && (
        <div className="rounded border border-amber-700 bg-amber-900/20 p-3 text-sm text-amber-200">
          <p className="font-medium">Recall requested — waiting for the admin.</p>
          <p className="mt-1 whitespace-pre-wrap text-amber-100">“{openReq.reason}”</p>
        </div>
      )}
      {declinedReq && !openReq && (
        <div className="rounded border border-gray-700 bg-gray-800/60 p-3 text-sm text-gray-300">
          <p className="font-medium text-gray-200">
            Recall request declined{declinedReq.resolved_by ? ` by ${declinedReq.resolved_by}` : ''}.
          </p>
          {declinedReq.resolution_note && (
            <p className="mt-1 whitespace-pre-wrap">{declinedReq.resolution_note}</p>
          )}
        </div>
      )}

      {state.can_recall && !openReq && (
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={busy}
            onClick={() => (state.auto ? doRecall('') : setModalOpen(true))}
            className="rounded border border-amber-600 px-3 py-1.5 text-sm font-medium text-amber-400 hover:bg-gray-700 disabled:opacity-50"
          >
            {busy ? 'Recalling…' : 'Recall submission'}
          </button>
          {!state.auto && (
            <p className="text-xs text-gray-500">
              {state.blocker === 'approved'
                ? 'Already approved — recalling sends a request the admin must grant.'
                : 'An admin is currently reviewing this trip — recalling sends a request instead of pulling it back.'}
            </p>
          )}
        </div>
      )}

      <Modal
        isOpen={modalOpen}
        onRequestClose={() => !busy && setModalOpen(false)}
        style={MODAL_STYLE}
        contentLabel="Request recall"
      >
        <h2 className="mb-2 text-sm font-semibold">Request recall</h2>
        <p className="mb-3 text-xs text-gray-400">
          {state.blocker === 'approved'
            ? 'This trip is already approved, so it can’t be pulled back automatically. Tell the admin why you need it back — they can send it back to you or make the fix themselves.'
            : 'An admin is reviewing this trip right now. Tell them why you need it back — they can send it back to you or make the fix themselves.'}
        </p>
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Why do you need this back? (e.g. Scene 4 narration has a mistake)"
          rows={4}
          autoFocus
          className="mb-3 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-base sm:text-sm"
        />
        <div className="flex justify-end gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => setModalOpen(false)}
            className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={busy || !reason.trim()}
            onClick={() => doRecall(reason.trim())}
            className="rounded border border-amber-600 px-3 py-1.5 text-sm text-amber-400 hover:bg-gray-700 disabled:opacity-50"
          >
            {busy ? 'Sending…' : 'Send request'}
          </button>
        </div>
      </Modal>
    </div>
  );
};

export default RecallControl;
