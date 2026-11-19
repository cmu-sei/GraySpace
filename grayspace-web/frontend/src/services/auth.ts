/**
 * The signed-in session's token (POST /api/v1/auth/login), kept for this browser tab.
 *
 * Every request to the server carries it as a bearer token, and websockets as ?token= since
 * browsers can't set their headers (docs/deployments.md, Sign-in). The server's service token
 * (GRAYSPACE_API_TOKEN) works the same way, for scripts. A 401 means the session ended: sign in again.
 */
const STORAGE_KEY = 'grayspace.session';
export const UNAUTHORIZED_EVENT = 'grayspace:unauthorized';

export const getToken = (): string | null => {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
};

export const setToken = (token: string): void => {
  window.sessionStorage.setItem(STORAGE_KEY, token.trim());
};

export const clearToken = (): void => {
  window.sessionStorage.removeItem(STORAGE_KEY);
};

export const authHeaders = (): Record<string, string> => {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
};

/** Browsers can't set headers on a websocket; the server also takes ?token=. */
export const withToken = (url: string): string => {
  const token = getToken();
  if (!token) return url;
  return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;
};

export const reportUnauthorized = (): void => {
  window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
};
