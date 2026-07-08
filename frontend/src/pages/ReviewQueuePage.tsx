import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import Modal from 'react-modal';
import { toast } from 'react-toastify';
import { api, ApiError, type RecallRequest, type ReviewQueueItem } from '../api';
import NavBar from '../components/NavBar';
import PresenceBadge from '../components/PresenceBadge';
import { usePresence } from '../usePresence';
import { MODAL_STYLE } from '../modalStyle';

const formatSubmittedAt = (t: number | null): string => {
  if (t === null) return '—';
  try {
    return new Date(t * 1000).toLocaleString();
  } catch {
    return '—';
  }
};

/** Admin-only: pinned recall requests on top, then the sessions currently `submitted`,
 * awaiting approve/send-back. Opening one reuses ChangesSummaryPage (/admin/:sid). */
const ReviewQueuePage = () => {
  const [items, setItems] = useState<ReviewQueueItem[] | null>(null);
  const [requests, setRequests] = useState<RecallRequest[]>([]);
  const [error, setError] = useState<string | null>(null);
  const presence = usePresence();

  // Resolve modal: which request + which action, with an optional note.
  const [resolveTarget, setResolveTarget] = useState<{ req: RecallRequest; action: 'grant' | 'decline' } | null>(null);
  const [resolveNote, setResolveNote] = useState('');
  const [resolving, setResolving] = useState(false);

  const load = useCallback(() => {
    api
      .reviewQueue()
      .then(setItems)
      .catch((e: unknown) => {
        setError(e instanceof ApiError ? e.detail || e.code : 'Failed to load the review queue');
        setItems([]);
      });
    api
      .recallRequests('open')
      .then(setRequests)
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const confirmResolve = () => {
    if (!resolveTarget) return;
    const { req, action } = resolveTarget;
    setResolving(true);
    api
      .resolveRecall(req.id, action, resolveNote.trim())
      .then(() => {
        toast.success(
          action === 'grant'
            ? `Sent "${req.title || req.trip_id}" back to ${req.requested_by}.`
            : 'Request declined — the requester will see your note.',
        );
        setResolveTarget(null);
        setResolveNote('');
        load();
      })
      .catch((e: unknown) => toast.error(`Failed: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setResolving(false));
  };

  return (
    <>
      <NavBar title="Review queue" subtitle="Sessions submitted and awaiting your approval" />
      <main className="mx-auto max-w-review space-y-4 px-4 py-6">
        {/* Pinned recall requests */}
        {requests.length > 0 && (
          <section className="overflow-hidden rounded-lg border border-amber-700 bg-amber-900/15">
            <p className="border-b border-amber-800/60 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-amber-300">
              📌 Recall requests ({requests.length})
            </p>
            <ul className="divide-y divide-amber-800/40">
              {requests.map((req) => (
                <li key={req.id} className="space-y-2 px-4 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-medium text-amber-100">{req.title || req.trip_id}</p>
                    {req.session_status === 'approved' && (
                      <span
                        className="rounded bg-red-800 px-2 py-0.5 text-[11px] font-medium text-white"
                        title="Approval already wrote staging and promoted the mp3 masters"
                      >
                        already approved
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-amber-300/80">
                    {req.trip_id} · {req.language} · requested by {req.requested_by} ·{' '}
                    {formatSubmittedAt(req.created_at)}
                  </p>
                  <p className="whitespace-pre-wrap text-sm text-amber-100">“{req.reason}”</p>
                  {req.session_status === 'approved' && (
                    <p className="text-xs text-red-300">
                      Granting un-completes the trip (Stage 9 stops seeing it) and re-opens it for the
                      reviewer; staging keeps the approved content until it is re-approved.
                    </p>
                  )}
                  <div className="flex flex-wrap gap-2 pt-1">
                    <button
                      type="button"
                      onClick={() => {
                        setResolveNote('');
                        setResolveTarget({ req, action: 'grant' });
                      }}
                      className="rounded border border-amber-600 px-3 py-1.5 text-xs font-medium text-amber-300 hover:bg-amber-900/30"
                    >
                      Send back to reviewer
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setResolveNote('');
                        setResolveTarget({ req, action: 'decline' });
                      }}
                      className="rounded border border-gray-600 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-700"
                    >
                      Decline (I’ll handle it)
                    </button>
                    <Link
                      to={`/admin/${req.sid}`}
                      className="rounded border border-gray-600 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-700"
                    >
                      Open session
                    </Link>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}

        {items === null && <p className="text-gray-400">Loading…</p>}

        {items !== null && error && (
          <div className="rounded border border-red-700 bg-red-900/30 p-3 text-sm text-red-300">{error}</div>
        )}

        {items !== null && items.length === 0 && !error && requests.length === 0 && (
          <p className="text-gray-400">Nothing awaiting approval right now.</p>
        )}

        {items !== null && items.length > 0 && (
          <ul className="divide-y divide-gray-700/60 overflow-hidden rounded-lg border border-gray-700 bg-gray-800/60">
            {items.map((it) => (
              <li key={it.sid} className="flex flex-wrap items-center justify-between gap-4 gap-y-2 px-4 py-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="truncate text-sm text-gray-200">{it.title || it.trip_id}</p>
                    {it.edit_required && (
                      <span
                        className="rounded bg-amber-600 px-2 py-0.5 text-[11px] font-medium text-white"
                        title="A field is flagged edit-required"
                      >
                        Edit required
                      </span>
                    )}
                    <PresenceBadge entries={presence.filter((p) => p.sid === it.sid)} />
                  </div>
                  <p className="truncate text-[11px] text-gray-500">
                    {it.trip_id} · {it.language} · submitted by {it.submitted_by ?? 'unknown'} ·{' '}
                    {formatSubmittedAt(it.submitted_at)}
                  </p>
                </div>
                <Link
                  to={`/admin/${it.sid}`}
                  className="shrink-0 rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
                >
                  Review
                </Link>
              </li>
            ))}
          </ul>
        )}
      </main>

      <Modal
        isOpen={resolveTarget !== null}
        onRequestClose={() => !resolving && setResolveTarget(null)}
        style={MODAL_STYLE}
        contentLabel="Resolve recall request"
      >
        <h2 className="mb-2 text-sm font-semibold">
          {resolveTarget?.action === 'grant' ? 'Send back to the reviewer' : 'Decline the recall request'}
        </h2>
        <p className="mb-3 text-xs text-gray-400">
          {resolveTarget?.action === 'grant' ? (
            resolveTarget.req.session_status === 'approved' ? (
              <>
                <span className="font-medium text-red-300">This trip is already approved</span> — granting
                un-completes it (removed from Stage 9’s completed list) and re-opens it as{' '}
                <span className="text-amber-300">changes requested</span>. Staging keeps the approved content
                until it is re-approved.
              </>
            ) : (
              <>
                The session re-opens for the reviewer as{' '}
                <span className="text-amber-300">changes requested</span>; without a note, their own reason is
                shown back to them.
              </>
            )
          ) : (
            'The submission stays locked for your review. The requester sees your note on their session page.'
          )}
        </p>
        <textarea
          value={resolveNote}
          onChange={(e) => setResolveNote(e.target.value)}
          placeholder={resolveTarget?.action === 'grant' ? 'Optional note to the reviewer' : 'Why not? (shown to the requester)'}
          rows={3}
          autoFocus
          className="mb-3 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-base sm:text-sm"
        />
        <div className="flex justify-end gap-2">
          <button
            type="button"
            disabled={resolving}
            onClick={() => setResolveTarget(null)}
            className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={resolving}
            onClick={confirmResolve}
            className={`rounded border px-3 py-1.5 text-sm disabled:opacity-50 ${
              resolveTarget?.action === 'grant'
                ? 'border-amber-600 text-amber-400 hover:bg-gray-700'
                : 'border-gray-600 text-gray-300 hover:bg-gray-700'
            }`}
          >
            {resolving ? 'Working…' : resolveTarget?.action === 'grant' ? 'Send back' : 'Decline'}
          </button>
        </div>
      </Modal>
    </>
  );
};

export default ReviewQueuePage;
