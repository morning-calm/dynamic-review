import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../authContext';
import { api, type AuthUser, type FindingsInbox } from '../api';

/** Current user + logout, the "Completed" link (both roles), the "Bug reports" link
 * (both roles, with an unread/open badge), the admin-only "Review queue" link, and the
 * ? help menu (guides open in a new tab, served by the backend from docs/user-guides). */
const UserMenu = () => {
  const { user, logout } = useAuth();
  const [bugBadge, setBugBadge] = useState(0);
  const [descBadge, setDescBadge] = useState(0);
  const [queueBadge, setQueueBadge] = useState(0);
  const [recallBadge, setRecallBadge] = useState(0);
  const [finalBadge, setFinalBadge] = useState(0);
  const [publisherMode, setPublisherMode] = useState(false);
  const [aiBadge, setAiBadge] = useState(0);
  const [aiSessions, setAiSessions] = useState<FindingsInbox['sessions']>([]);
  const [aiOpen, setAiOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    const load = () =>
      api
        .bugCounts()
        .then((c) => {
          if (!cancelled) setBugBadge(c.open ?? c.unread ?? 0);
        })
        .catch(() => {});
    load();
    // Light polling so a new report / reply surfaces without a reload.
    const t = setInterval(load, 60000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [user]);

  // AI-review triage waiting on THIS user (a reviewer sees their own submissions; an admin
  // sees every trip parked with a reviewer). One waiting session links straight into it —
  // the common case. Several open a picker: the old fallback linked to '/', which from the
  // trip list itself navigated nowhere and read as a dead button (dave, 2026-08-05).
  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    const load = () =>
      api
        .getFindingsInbox()
        .then((inbox) => {
          if (cancelled) return;
          setAiBadge(inbox.count);
          setAiSessions(inbox.sessions);
        })
        .catch(() => {});
    load();
    const t = setInterval(load, 60000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [user]);

  // Trip-description items waiting on THIS user (admin: English checks; reviewer:
  // translations in their languages).
  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    const load = () =>
      api
        .tripDescCounts()
        .then((c) => {
          if (!cancelled) setDescBadge(c.open);
        })
        .catch(() => {});
    load();
    const t = setInterval(load, 60000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [user]);

  // Admin-only, once: is this instance the workstation publisher (shows the console link).
  useEffect(() => {
    if (!user || user.role !== 'admin') return;
    let cancelled = false;
    api
      .publisherMode()
      .then((r) => {
        if (!cancelled) setPublisherMode(r.publisher_mode);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [user]);

  // Admin-only: trips with open final checks → badge on the Final check link.
  useEffect(() => {
    if (!user || user.role !== 'admin') return;
    let cancelled = false;
    const load = () =>
      api
        .finalCount()
        .then((c) => {
          if (!cancelled) setFinalBadge(c.open);
        })
        .catch(() => {});
    load();
    const t = setInterval(load, 60000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [user]);

  // Admin-only: two counts on the Review queue link — submitted sessions awaiting
  // approval (amber) and open RECALL REQUESTS (rose; a reviewer is blocked waiting
  // on the admin's grant/decline, so it must be visible from anywhere, dave
  // 2026-08-22).
  useEffect(() => {
    if (!user || user.role !== 'admin') return;
    let cancelled = false;
    const load = () => {
      api
        .reviewQueueCount()
        .then((c) => {
          if (!cancelled) setQueueBadge(c.open);
        })
        .catch(() => {});
      api
        .recallCounts()
        .then((c) => {
          if (!cancelled) setRecallBadge(c.open);
        })
        .catch(() => {});
    };
    load();
    const t = setInterval(load, 60000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [user]);

  if (!user) return null;

  // Reviewers get their guide in their own language too (served per-user by the backend).
  const nativeLabel = user.role !== 'admin' && user.languages.includes('Japanese')
    ? 'ガイド（日本語）'
    : user.role !== 'admin' && user.languages.includes('Mandarin')
      ? '指南（中文）'
      : null;
  const helpItem =
    'block whitespace-nowrap rounded px-3 py-1.5 text-left text-gray-200 hover:bg-gray-700';

  return (
    <>
      {/* Mobile: everything folds into a single ⋮ popout so the banner stays short and
          leaves the screen to the editing surface. Desktop keeps the inline row below. */}
      <MobileMenu
        user={user}
        logout={logout}
        nativeLabel={nativeLabel}
        bugBadge={bugBadge}
        descBadge={descBadge}
        queueBadge={queueBadge}
        recallBadge={recallBadge}
        finalBadge={finalBadge}
        publisherMode={publisherMode}
        aiBadge={aiBadge}
        aiSessions={aiSessions}
      />

      <div className="hidden flex-wrap items-center justify-end gap-2 gap-y-1 text-xs sm:flex">
      <div className="relative">
        <button
          type="button"
          onClick={() => setHelpOpen((o) => !o)}
          title="Help — open the user guides in a new tab"
          aria-haspopup="menu"
          aria-expanded={helpOpen}
          className="rounded-full border border-gray-600 px-2 py-1 font-semibold text-gray-200 hover:bg-gray-700"
        >
          ?
        </button>
        {helpOpen && (
          <>
            {/* click-away backdrop */}
            <div className="fixed inset-0 z-30" onClick={() => setHelpOpen(false)} />
            <div className="absolute right-0 z-40 mt-1 rounded border border-gray-700 bg-gray-900 py-1 shadow-lg">
              <a href="/help/quick" target="_blank" rel="noreferrer" className={helpItem} onClick={() => setHelpOpen(false)}>
                Quick reference (1 page)
              </a>
              <a href="/help/guide" target="_blank" rel="noreferrer" className={helpItem} onClick={() => setHelpOpen(false)}>
                User guide (English)
              </a>
              {nativeLabel && (
                <a href="/help/guide-native" target="_blank" rel="noreferrer" className={helpItem} onClick={() => setHelpOpen(false)}>
                  {nativeLabel}
                </a>
              )}
            </div>
          </>
        )}
      </div>
      {user.role === 'admin' && (
        <Link
          to="/queue"
          className="relative rounded border border-gray-600 px-2 py-1 text-gray-200 hover:bg-gray-700"
          title={
            [
              queueBadge > 0
                ? `${queueBadge} submitted trip${queueBadge === 1 ? '' : 's'} awaiting approval`
                : '',
              recallBadge > 0
                ? `${recallBadge} open recall request${recallBadge === 1 ? '' : 's'}`
                : '',
            ]
              .filter(Boolean)
              .join(' · ') || undefined
          }
        >
          Review queue
          {queueBadge > 0 && (
            <span className="ml-1 rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] font-semibold text-gray-900">
              {queueBadge}
            </span>
          )}
          {recallBadge > 0 && (
            <span className="ml-1 rounded-full bg-rose-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">
              {recallBadge}
            </span>
          )}
        </Link>
      )}
      {(user.role === 'admin' || descBadge > 0) && (
        <Link
          to="/descriptions"
          className="relative rounded border border-gray-600 px-2 py-1 text-gray-200 hover:bg-gray-700"
          title={
            user.role === 'admin'
              ? 'Family trip descriptions — English check & translations'
              : `${descBadge} trip description${descBadge === 1 ? '' : 's'} waiting for your review`
          }
        >
          Descriptions
          {descBadge > 0 && (
            <span className="ml-1 rounded-full bg-teal-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">
              {descBadge}
            </span>
          )}
        </Link>
      )}
      {aiBadge > 0 && aiSessions.length === 1 && (
        <Link
          to={`/review/${aiSessions[0].session_id}`}
          className="relative rounded border border-purple-600 bg-purple-900/30 px-2 py-1 text-purple-100 hover:bg-purple-800/50"
          title={`${aiBadge} AI-review item${aiBadge === 1 ? '' : 's'} waiting for your response`}
        >
          AI review
          <span className="ml-1 rounded-full bg-purple-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">
            {aiBadge}
          </span>
        </Link>
      )}
      {aiBadge > 0 && aiSessions.length > 1 && (
        <div className="relative">
          <button
            type="button"
            onClick={() => setAiOpen((o) => !o)}
            aria-haspopup="menu"
            aria-expanded={aiOpen}
            className="relative rounded border border-purple-600 bg-purple-900/30 px-2 py-1 text-purple-100 hover:bg-purple-800/50"
            title={`${aiBadge} AI-review item${aiBadge === 1 ? '' : 's'} across ${aiSessions.length} trips — click to pick one`}
          >
            AI review
            <span className="ml-1 rounded-full bg-purple-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">
              {aiBadge}
            </span>
          </button>
          {aiOpen && (
            <>
              {/* click-away backdrop (same pattern as the help menu) */}
              <div className="fixed inset-0 z-30" onClick={() => setAiOpen(false)} />
              <div className="absolute right-0 z-40 mt-1 min-w-64 rounded border border-gray-700 bg-gray-900 py-1 shadow-lg">
                {aiSessions.map((s) => (
                  <Link
                    key={s.session_id}
                    to={`/review/${s.session_id}`}
                    onClick={() => setAiOpen(false)}
                    className="flex items-center justify-between gap-3 whitespace-nowrap rounded px-3 py-1.5 text-left text-gray-200 hover:bg-gray-700"
                  >
                    <span>
                      {s.trip_id}
                      {s.submitted_by && <span className="text-gray-500"> · {s.submitted_by}</span>}
                    </span>
                    <span className="rounded-full bg-purple-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                      {s.open}
                    </span>
                  </Link>
                ))}
              </div>
            </>
          )}
        </div>
      )}
      {user.role === 'admin' && (
        <Link
          to="/final-check"
          className="relative rounded border border-gray-600 px-2 py-1 text-gray-200 hover:bg-gray-700"
          title="Release preparation checks before publish (lanes 10–11)"
        >
          Release prep
          {finalBadge > 0 && (
            <span className="ml-1 rounded-full bg-indigo-500 px-1.5 py-0.5 text-[10px] font-semibold text-white">
              {finalBadge}
            </span>
          )}
        </Link>
      )}
      {user.role === 'admin' && publisherMode && (
        <Link
          to="/publisher"
          className="rounded border border-rose-800 bg-rose-900/20 px-2 py-1 text-rose-200 hover:bg-rose-900/40"
          title="Workstation publish console — staging → PRODUCTION"
        >
          Publisher
        </Link>
      )}
      <Link to="/completed" className="rounded border border-gray-600 px-2 py-1 text-gray-200 hover:bg-gray-700">
        Completed
      </Link>
      {user.role === 'admin' && (
        <Link
          to="/staging"
          className="rounded border border-gray-600 px-2 py-1 text-gray-200 hover:bg-gray-700"
          title="Search & open any staging trip (admin)"
        >
          All trips
        </Link>
      )}
      <Link
        to="/bugs"
        className="relative rounded border border-gray-600 px-2 py-1 text-gray-200 hover:bg-gray-700"
        title={user.role === 'admin' ? 'Open bug reports' : 'Your bug reports & replies'}
      >
        Bug reports
        {bugBadge > 0 && (
          <span className="ml-1 rounded-full bg-rose-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">
            {bugBadge}
          </span>
        )}
      </Link>
      <span
        className="hidden text-gray-400 sm:inline"
        title={user.role === 'admin' ? 'all languages' : user.languages.join(', ')}
      >
        {user.username} <span className="text-gray-600">·</span> {user.role}
      </span>
      <button type="button" onClick={logout} className="text-gray-400 underline hover:text-gray-200">
        Log out
      </button>
      </div>
    </>
  );
};

/** Mobile-only (`sm:hidden`) condensed nav: a single ⋮ button opening a popout with
 * every link the desktop row shows, so the sticky banner stays one short line on phones. */
const MobileMenu = ({
  user,
  logout,
  nativeLabel,
  bugBadge,
  descBadge,
  queueBadge,
  recallBadge,
  finalBadge,
  publisherMode,
  aiBadge,
  aiSessions,
}: {
  user: AuthUser;
  logout: () => void;
  nativeLabel: string | null;
  bugBadge: number;
  descBadge: number;
  queueBadge: number;
  recallBadge: number;
  finalBadge: number;
  publisherMode: boolean;
  aiBadge: number;
  aiSessions: FindingsInbox['sessions'];
}) => {
  const [open, setOpen] = useState(false);
  // The AI-review trips fold into their own sub-list — with many trips waiting they
  // used to flood the whole ⋮ menu and drown the other links (dave, 2026-08-21).
  const [aiListOpen, setAiListOpen] = useState(false);
  const close = () => {
    setOpen(false);
    setAiListOpen(false);
  };
  // Unseen-activity dot on the closed ⋮ (the admin-only counts only for admins).
  const totalBadge =
    bugBadge + aiBadge + descBadge +
    (user.role === 'admin' ? queueBadge + recallBadge + finalBadge : 0);
  const item = 'flex items-center justify-between gap-3 rounded px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-700';
  const badge = (n: number, cls: string) =>
    n > 0 ? <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${cls}`}>{n}</span> : null;

  return (
    <div className="relative sm:hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Menu"
        className="relative rounded border border-gray-600 px-2 py-1.5 text-lg leading-none text-gray-200 hover:bg-gray-700"
      >
        ⋮
        {!open && totalBadge > 0 && (
          <span className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full bg-rose-600" />
        )}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={close} />
          <div className="absolute right-0 z-40 mt-1 w-56 rounded border border-gray-700 bg-gray-900 p-1 shadow-lg">
            <div className="border-b border-gray-800 px-3 py-2 text-xs text-gray-400">
              {user.username} <span className="text-gray-600">·</span> {user.role}
            </div>
            {user.role === 'admin' && (
              <Link to="/queue" className={item} onClick={close}>
                <span>Review queue</span>
                <span className="flex items-center gap-1">
                  {badge(queueBadge, 'bg-amber-500 text-gray-900')}
                  {badge(recallBadge, 'bg-rose-600 text-white')}
                </span>
              </Link>
            )}
            {(user.role === 'admin' || descBadge > 0) && (
              <Link to="/descriptions" className={item} onClick={close}>
                <span>Descriptions</span>
                {badge(descBadge, 'bg-teal-600 text-white')}
              </Link>
            )}
            {/* One waiting trip links straight in; several fold into an expandable
                sub-list (a flat row per trip used to drown the rest of this menu). */}
            {aiSessions.length === 1 && (
              <Link to={`/review/${aiSessions[0].session_id}`} className={item} onClick={close}>
                <span className="truncate">AI review · {aiSessions[0].trip_id}</span>
                {badge(aiSessions[0].open, 'bg-purple-600 text-white')}
              </Link>
            )}
            {aiSessions.length > 1 && (
              <>
                <button
                  type="button"
                  onClick={() => setAiListOpen((o) => !o)}
                  aria-expanded={aiListOpen}
                  className={`${item} w-full`}
                >
                  <span>
                    <span aria-hidden="true" className="mr-1 inline-block text-gray-500">
                      {aiListOpen ? '▾' : '▸'}
                    </span>
                    AI review · {aiSessions.length} trips
                  </span>
                  {badge(aiBadge, 'bg-purple-600 text-white')}
                </button>
                {aiListOpen && (
                  <div className="max-h-64 overflow-y-auto border-l border-gray-800 pl-2">
                    {aiSessions.map((s) => (
                      <Link key={s.session_id} to={`/review/${s.session_id}`} className={item} onClick={close}>
                        <span className="truncate">{s.trip_id}</span>
                        {badge(s.open, 'bg-purple-600 text-white')}
                      </Link>
                    ))}
                  </div>
                )}
              </>
            )}
            {user.role === 'admin' && (
              <Link to="/final-check" className={item} onClick={close}>
                <span>Release prep</span>
                {badge(finalBadge, 'bg-indigo-500 text-white')}
              </Link>
            )}
            {user.role === 'admin' && publisherMode && (
              <Link to="/publisher" className={item} onClick={close}>
                Publisher
              </Link>
            )}
            <Link to="/completed" className={item} onClick={close}>
              Completed
            </Link>
            {user.role === 'admin' && (
              <Link to="/staging" className={item} onClick={close}>
                All trips
              </Link>
            )}
            <Link to="/bugs" className={item} onClick={close}>
              <span>Bug reports</span>
              {badge(bugBadge, 'bg-rose-600 text-white')}
            </Link>
            <div className="my-1 border-t border-gray-800" />
            <a href="/help/quick" target="_blank" rel="noreferrer" className={item} onClick={close}>
              Quick reference
            </a>
            <a href="/help/guide" target="_blank" rel="noreferrer" className={item} onClick={close}>
              User guide
            </a>
            {nativeLabel && (
              <a href="/help/guide-native" target="_blank" rel="noreferrer" className={item} onClick={close}>
                {nativeLabel}
              </a>
            )}
            <div className="my-1 border-t border-gray-800" />
            <button
              type="button"
              onClick={() => {
                close();
                logout();
              }}
              className={`${item} w-full`}
            >
              Log out
            </button>
          </div>
        </>
      )}
    </div>
  );
};

export default UserMenu;
