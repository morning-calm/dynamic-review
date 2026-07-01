import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../authContext';
import { api } from '../api';

/** Current user + logout, the "Completed" link (both roles), the "Bug reports" link
 * (both roles, with an unread/open badge), and the admin-only "Review queue" link. */
const UserMenu = () => {
  const { user, logout } = useAuth();
  const [bugBadge, setBugBadge] = useState(0);

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

  if (!user) return null;

  return (
    <div className="flex shrink-0 items-center gap-3 text-xs">
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
          to="/queue"
          className="rounded border border-gray-600 px-2 py-1 text-gray-200 hover:bg-gray-700"
        >
          Review queue
        </Link>
      )}
      <span className="text-gray-400" title={user.role === 'admin' ? 'all languages' : user.languages.join(', ')}>
        {user.username} <span className="text-gray-600">·</span> {user.role}
      </span>
      <button type="button" onClick={logout} className="text-gray-400 underline hover:text-gray-200">
        Log out
      </button>
    </div>
  );
};

export default UserMenu;
