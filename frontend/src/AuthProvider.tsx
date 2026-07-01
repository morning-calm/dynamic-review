import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, clearToken, getToken, setToken, setUnauthorizedHandler, type AuthUser } from './api';
import { AuthContext, type AuthState, type AuthStatus } from './authContext';

/**
 * Bootstraps auth from a persisted token (GET /api/me), exposes login/logout,
 * and registers the central 401 handler (api.ts calls this on any 401 from
 * anywhere in the app — it clears local state so route guards bounce to
 * Login). Mount once, above the router.
 */
export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>('loading');

  useEffect(() => {
    if (!getToken()) {
      setStatus('anonymous');
      return;
    }
    let cancelled = false;
    api
      .me()
      .then((u) => {
        if (cancelled) return;
        setUser(u);
        setStatus('authenticated');
      })
      .catch(() => {
        if (cancelled) return;
        clearToken();
        setUser(null);
        setStatus('anonymous');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser(null);
      setStatus('anonymous');
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const res = await api.login(username, password);
    setToken(res.token);
    setUser(res.user);
    setStatus('authenticated');
  }, []);

  const logout = useCallback(() => {
    void api.logout().catch(() => {
      /* best effort — the token is invalidated locally regardless */
    });
    clearToken();
    setUser(null);
    setStatus('anonymous');
  }, []);

  // token isn't its own useState — it's read straight from api.ts's module-level
  // holder (the single source of truth every request actually uses). It's safe
  // to recompute inside this memo because every call site that changes the
  // token (login/logout/bootstrap) also changes `status`/`user` in the same
  // breath, so this always recomputes in step.
  const value = useMemo<AuthState>(
    () => ({ status, user, token: getToken(), login, logout }),
    [status, user, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};
