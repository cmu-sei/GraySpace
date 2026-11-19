/**
 * Who is signed in (GET /api/v1/me) and what they may do (docs/deployments.md, Access).
 *
 * Components ask can(permission), never the role: server-wide permissions come from /me, a
 * deployment's from its my_access (canIn). A 401 anywhere means the session ended: the token is
 * forgotten and RequireAuth sends the user to sign in.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { control } from '../api/control';
import { clearToken, getToken, setToken, UNAUTHORIZED_EVENT } from '../services/auth';
import { Deployment, LoginResponse, Me } from '../types/control';

interface Auth {
  me: Me | null;
  loading: boolean;
  signIn: (username: string, password: string) => Promise<LoginResponse>;
  signOut: () => Promise<void>;
  refresh: () => Promise<Me | null>;
  can: (permission: string) => boolean;
}

const AuthContext = createContext<Auth | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(Boolean(getToken()));

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setMe(null);
      return null;
    }
    try {
      const found = await control.me();
      setMe(found);
      return found;
    } catch {
      clearToken();
      setMe(null);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const ended = () => {
      clearToken();
      setMe(null);
    };
    window.addEventListener(UNAUTHORIZED_EVENT, ended);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, ended);
  }, [refresh]);

  const signIn = useCallback(async (username: string, password: string) => {
    const response = await control.login(username, password);
    setToken(response.token);
    await refresh();
    return response;
  }, [refresh]);

  const signOut = useCallback(async () => {
    try {
      await control.logout();
    } catch {
      // the session may have ended already
    }
    clearToken();
    setMe(null);
  }, []);

  const value = useMemo<Auth>(() => ({
    me, loading, signIn, signOut, refresh, can: (permission) => Boolean(me?.permissions.includes(permission)),
  }), [me, loading, signIn, signOut, refresh]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

/** The signed-in user's auth, or null outside an AuthProvider (a page rendered on its own). */
export const useOptionalAuth = (): Auth | null => useContext(AuthContext);

export const useAuth = (): Auth => {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error('useAuth needs an AuthProvider');
  return auth;
};

/** Whether the deployment's my_access allows the permission (read, operate, build, members:manage, delete). */
export const canIn = (deployment: Deployment | null | undefined, permission: string): boolean =>
  Boolean(deployment?.my_access.includes(permission));
