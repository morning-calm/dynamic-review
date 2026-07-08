import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../authContext';
import { api } from '../api';

/** Current user + logout, the "Completed" link (both roles), the "Bug reports" link
 * (both roles, with an unread/open badge), the admin-only "Review queue" link, and the
 * ? help menu (guides open in a new tab, served by the backend from docs/user-guides). */
const UserMenu = () => {
  const { user, logout } = useAuth();
  const [bugBadge, setBugBadge] = useState(0);
  const [recallBadge, setRecallBadge] = useState(0);
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

  // Admin-only: open recall requests → badge on the Review queue link.
  useEffect(() => {
    if (!user || user.role !== 'admin') return;
    let cancelled = false;
    const load = () =>
      api
        .recallCounts()
        .then((c) => {
          if (!cancelled) setRecallBadge(c.open);
        })
        .catch(() => {});
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
    <div className="flex flex-wrap items-center justify-end gap-2 gap-y-1 text-xs">
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
      {user.role === 'admin' && (
        <Link
          to="/queue"
          className="relative rounded border border-gray-600 px-2 py-1 text-gray-200 hover:bg-gray-700"
          title={recallBadge > 0 ? `${recallBadge} recall request${recallBadge === 1 ? '' : 's'} waiting` : undefined}
        >
          Review queue
          {recallBadge > 0 && (
            <span className="ml-1 rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] font-semibold text-gray-900">
              {recallBadge}
            </span>
          )}
        </Link>
      )}
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
  );
};

export default UserMenu;
