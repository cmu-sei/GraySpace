/**
 * Sign in with a local account (POST /api/v1/auth/login). "Sign in with SSO" appears above the
 * form once the server enables OIDC (GET /api/v1/auth/providers).
 */
import React, { useEffect, useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { control } from '../api/control';
import { APIError } from '../services/api';
import { useAuth } from './AuthProvider';

export const SignInPage: React.FC = () => {
  const { me, signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from || '/';
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [providers, setProviders] = useState<string[]>(['local']);

  useEffect(() => {
    control.providers().then((p) => setProviders(p.providers)).catch(() => undefined);
  }, []);

  if (me && !me.must_change_password) return <Navigate to={from} replace />;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await signIn(username.trim(), password);
      navigate(response.must_change_password ? '/signin/change-password' : from, { replace: true, state: { from } });
    } catch (err) {
      if (err instanceof APIError && err.status === 429) setError(err.message);
      else if (err instanceof APIError && (err.status === 401 || err.status === 403)) setError(err.message);
      else setError(`Couldn't sign in: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="gs-login">
      <form className="gs-login-panel" onSubmit={submit}>
        <div className="gs-logo"><i />GraySpace</div>
        <div className="gs-muted" style={{ marginBottom: 14 }}>Internet backbone simulation</div>
        {providers.includes('oidc') && (
          <>
            <button type="button" className="gs-btn" disabled title="Not available yet">Sign in with SSO</button>
            <div className="gs-or">or a local account</div>
          </>
        )}
        <div className="gs-field">
          <label htmlFor="username">Username</label>
          <input id="username" autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
        </div>
        <div className="gs-field">
          <label htmlFor="password">Password</label>
          <input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {error && <div className="gs-banner bad" role="alert">{error}</div>}
        <button className="gs-btn" type="submit" disabled={busy || !username || !password}>{busy ? 'Signing in…' : 'Sign in'}</button>
        <div className="gs-muted" style={{ fontSize: 11, marginTop: 10 }}>Forgot your password? Ask an admin to reset it.</div>
      </form>
    </div>
  );
};
