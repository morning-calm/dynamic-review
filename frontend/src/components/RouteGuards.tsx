import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../authContext';

/** Gate for the whole app: anonymous → Login (remembering where they were
 * headed); still bootstrapping the persisted token → a blank loading state
 * (never flash Login then immediately redirect away). */
export const RequireAuth = () => {
  const { status } = useAuth();
  const location = useLocation();

  if (status === 'loading') {
    return <div className="flex min-h-screen items-center justify-center text-sm text-gray-400">Loading…</div>;
  }
  if (status === 'anonymous') {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return <Outlet />;
};

/** Nested inside RequireAuth, so `user` is always set by the time this runs.
 * Reviewers never see admin-only routes (the review queue) — bounce home. */
export const RequireAdmin = () => {
  const { user } = useAuth();
  if (user?.role !== 'admin') return <Navigate to="/" replace />;
  return <Outlet />;
};
