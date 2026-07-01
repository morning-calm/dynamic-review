import { useState, type FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { ApiError } from '../api';
import { useAuth } from '../authContext';

interface LocationState {
  from?: { pathname: string };
}

const LoginPage = () => {
  const { status, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Already logged in (e.g. back button to /login) — bounce straight through.
  // A declarative <Navigate> (not an imperative navigate() call) so this is
  // safe to render mid-render-pass.
  if (status === 'authenticated') {
    const from = (location.state as LocationState | null)?.from?.pathname ?? '/';
    return <Navigate to={from} replace />;
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) {
      setError('Enter a username and password.');
      return;
    }
    setBusy(true);
    setError(null);
    login(username.trim(), password)
      .then(() => {
        const from = (location.state as LocationState | null)?.from?.pathname ?? '/';
        navigate(from, { replace: true });
      })
      .catch((e: unknown) => {
        setError(e instanceof ApiError && e.status === 401 ? 'Incorrect username or password.' : 'Login failed — is the backend running?');
      })
      .finally(() => setBusy(false));
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-lg border border-gray-700 bg-gray-800/60 p-6 shadow-sm">
        <h1 className="mb-1 text-xl font-semibold text-white">Trip review</h1>
        <p className="mb-5 text-sm text-gray-400">Sign in to review or approve staged trip content.</p>

        <form onSubmit={onSubmit} className="space-y-3">
          <div>
            <label htmlFor="username" className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-400">
              Username
            </label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 outline-none focus:border-custom-green"
            />
          </div>
          <div>
            <label htmlFor="password" className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-400">
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 outline-none focus:border-custom-green"
            />
          </div>

          {error && <p className="rounded border border-red-700 bg-red-900/30 p-2 text-xs text-red-300">{error}</p>}

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded bg-custom-green px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {busy ? 'Signing in…' : 'Log in'}
          </button>
        </form>
      </div>
    </div>
  );
};

export default LoginPage;
