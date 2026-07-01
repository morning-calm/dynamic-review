import { createContext, useContext } from 'react';
import type { AuthUser } from './api';

/** `loading` = bootstrapping from a persisted token (GET /api/me in flight);
 * `anonymous` = no valid session, render Login; `authenticated` = `user` is set. */
export type AuthStatus = 'loading' | 'anonymous' | 'authenticated';

export interface AuthState {
  status: AuthStatus;
  user: AuthUser | null;
  /** The opaque bearer token (mirrors api.ts's getToken(), which is what every
   * request actually sends — this is exposed for any consumer that needs the
   * raw value, e.g. building a non-fetch authenticated URL). */
  token: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

export const AuthContext = createContext<AuthState | null>(null);

/** Must be used within <AuthProvider> (mounted once, in App.tsx). */
export const useAuth = (): AuthState => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
};
