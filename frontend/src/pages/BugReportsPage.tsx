import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'react-toastify';
import { api, ApiError, type BugReport, type BugStatusValue } from '../api';
import { useAuth } from '../authContext';
import NavBar from '../components/NavBar';

const STATUS_STYLE: Record<BugStatusValue, string> = {
  open: 'bg-rose-600 text-white',
  investigating: 'bg-amber-500 text-white',
  resolved: 'bg-emerald-600 text-white',
};

const when = (t: number | null): string => {
  if (!t) return '—';
  try {
    return new Date(t * 1000).toLocaleString();
  } catch {
    return '—';
  }
};

/** Bug-report inbox. Admin sees/triages every report; a reviewer sees only their own. Both can
 * reply in a thread; only the admin changes status. Selecting a report loads its detail (the
 * reporter's description, the snapshot text + audio, and the reply thread). */
const BugReportsPage = () => {
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const [list, setList] = useState<BugReport[] | null>(null);
  const [sel, setSel] = useState<BugReport | null>(null);
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);
  // On phones the grid stacks list-over-detail, so a tapped report opens below the whole
  // list; scroll it into view (desktop's side-by-side layout never needs this).
  const detailRef = useRef<HTMLElement | null>(null);
  const selId = sel?.id;
  useEffect(() => {
    if (selId == null) return;
    if (!window.matchMedia('(max-width: 767px)').matches) return;
    detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [selId]);

  const refreshList = useCallback(() => {
    api
      .listBugReports()
      .then(setList)
      .catch((e: unknown) => {
        toast.error(e instanceof ApiError ? e.detail : 'Failed to load bug reports');
        setList([]);
      });
  }, []);

  useEffect(() => refreshList(), [refreshList]);

  const open = (rid: number) => {
    api
      .getBugReport(rid)
      .then(setSel)
      .catch((e: unknown) => toast.error(e instanceof ApiError ? e.detail : 'Failed to load report'));
  };

  const sendReply = () => {
    if (!sel || !reply.trim()) return;
    setBusy(true);
    api
      .replyBugReport(sel.id, reply.trim())
      .then((r) => {
        setSel(r);
        setReply('');
        refreshList();
      })
      .catch((e: unknown) => toast.error(e instanceof ApiError ? e.detail : 'Reply failed'))
      .finally(() => setBusy(false));
  };

  const changeStatus = (status: BugStatusValue) => {
    if (!sel) return;
    setBusy(true);
    api
      .setBugStatus(sel.id, status)
      .then((r) => {
        setSel(r);
        refreshList();
      })
      .catch((e: unknown) => toast.error(e instanceof ApiError ? e.detail : 'Status change failed'))
      .finally(() => setBusy(false));
  };

  const snap = sel?.text_snapshot;
  const loc = snap?.localization;

  return (
    <>
      <NavBar
        title="Bug reports"
        subtitle={isAdmin ? 'Every reported problem — triage, reply, resolve' : 'Problems you reported, and replies'}
      />
      <main className="mx-auto grid max-w-review gap-4 px-4 py-6 md:grid-cols-[minmax(0,340px)_1fr]">
        {/* List */}
        <section className="space-y-2">
          {list === null && <p className="text-gray-400">Loading…</p>}
          {list !== null && list.length === 0 && <p className="text-gray-400">No bug reports.</p>}
          <ul className="space-y-2">
            {list?.map((b) => (
              <li key={b.id}>
                <button
                  type="button"
                  onClick={() => open(b.id)}
                  className={`w-full rounded-lg border p-3 text-left ${
                    sel?.id === b.id ? 'border-custom-green bg-gray-800' : 'border-gray-700 bg-gray-800/60 hover:bg-gray-800'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className={`rounded px-2 py-0.5 text-[11px] font-medium ${STATUS_STYLE[b.status]}`}>{b.status}</span>
                    <span className="text-[11px] text-gray-500">#{b.id}</span>
                  </div>
                  <p className="mt-1 truncate text-sm text-gray-200">{b.body}</p>
                  <p className="mt-0.5 truncate text-[11px] text-gray-500">
                    {b.trip_id} · {b.field_path}
                    {b.scene_index !== null ? ` (scene ${b.scene_index})` : ''} · {b.reporter}
                    {b.message_count > 0 ? ` · ${b.message_count} repl${b.message_count === 1 ? 'y' : 'ies'}` : ''}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        </section>

        {/* Detail */}
        <section ref={detailRef} className="min-w-0 scroll-mt-16">
          {!sel && <p className="text-gray-500">Select a report to view it.</p>}
          {sel && (
            <div className="space-y-4 rounded-lg border border-gray-700 bg-gray-800/60 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <h2 className="truncate text-sm font-semibold text-white">
                    {sel.trip_id} · {sel.field_path}
                    {sel.scene_index !== null ? ` (scene ${sel.scene_index})` : ''}
                  </h2>
                  <p className="text-[11px] text-gray-500">
                    #{sel.id} · reported by {sel.reporter} · {when(sel.created_at)}
                  </p>
                </div>
                <span className={`rounded px-2 py-0.5 text-[11px] font-medium ${STATUS_STYLE[sel.status]}`}>{sel.status}</span>
              </div>

              {isAdmin && (
                <div className="flex flex-wrap gap-2">
                  {(['open', 'investigating', 'resolved'] as BugStatusValue[]).map((s) => (
                    <button
                      key={s}
                      type="button"
                      disabled={busy || sel.status === s}
                      onClick={() => changeStatus(s)}
                      className="rounded border border-gray-600 px-2 py-1 text-xs text-gray-200 hover:bg-gray-700 disabled:opacity-40"
                    >
                      Mark {s}
                    </button>
                  ))}
                </div>
              )}

              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">The problem</p>
                <p className="mt-1 whitespace-pre-wrap text-sm text-gray-200">{sel.body}</p>
              </div>

              {/* Snapshot: what they saw/heard at report time */}
              <div className="rounded border border-gray-800 bg-gray-900/50 p-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">Captured at report time</p>
                {loc ? (
                  <dl className="mt-2 space-y-1 text-sm">
                    {(['Hans', 'Hant', 'zhuyin', 'en'] as const).map((k) =>
                      loc.cur?.[k] ? (
                        <div key={k} className="flex gap-2">
                          <dt className="w-16 shrink-0 text-gray-500">{k}</dt>
                          <dd className="text-gray-200">{loc.cur[k]}</dd>
                        </div>
                      ) : null,
                    )}
                  </dl>
                ) : (
                  <p className="mt-1 whitespace-pre-wrap text-sm text-gray-200">{snap?.current_text || '—'}</p>
                )}
                {(sel.audio.working || sel.audio.candidate) && (
                  <div className="mt-3 space-y-2">
                    {sel.audio.working && (
                      <div>
                        <p className="text-[11px] text-gray-500">working take</p>
                        <audio src={sel.audio.working} controls preload="none" className="h-8 w-full" />
                      </div>
                    )}
                    {sel.audio.candidate && (
                      <div>
                        <p className="text-[11px] text-gray-500">pending candidate</p>
                        <audio src={sel.audio.candidate} controls preload="none" className="h-8 w-full" />
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Thread */}
              <div className="space-y-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">Replies</p>
                {sel.messages && sel.messages.length > 0 ? (
                  sel.messages.map((m, i) => (
                    <div key={i} className="rounded border border-gray-800 bg-gray-900/40 p-2">
                      <p className="text-[11px] text-gray-500">
                        {m.author} <span className="text-gray-600">·</span> {m.author_role} <span className="text-gray-600">·</span> {when(m.created_at)}
                      </p>
                      <p className="mt-0.5 whitespace-pre-wrap text-sm text-gray-200">{m.body}</p>
                    </div>
                  ))
                ) : (
                  <p className="text-sm text-gray-500">No replies yet.</p>
                )}
              </div>

              {/* Reply box */}
              <div>
                <textarea
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  placeholder={isAdmin ? 'Reply to the reporter (any language)…' : 'Add a reply (any language)…'}
                  rows={3}
                  className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-base sm:text-sm"
                />
                <div className="mt-2 flex justify-end">
                  <button
                    type="button"
                    disabled={busy || !reply.trim()}
                    onClick={sendReply}
                    className="rounded border border-custom-green px-3 py-1 text-sm text-custom-green hover:bg-gray-700 disabled:opacity-40"
                  >
                    Send reply
                  </button>
                </div>
              </div>
            </div>
          )}
        </section>
      </main>
    </>
  );
};

export default BugReportsPage;
