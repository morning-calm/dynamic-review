import { Link } from 'react-router-dom';
import { useAuth } from '../authContext';

/** Current user + logout, plus the admin-only "Review queue" link. Reviewers
 * never see the queue link (nav gating from /api/me's role). */
const UserMenu = () => {
  const { user, logout } = useAuth();
  if (!user) return null;

  return (
    <div className="flex shrink-0 items-center gap-3 text-xs">
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
