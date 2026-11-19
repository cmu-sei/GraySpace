/** First sign-in with a one-time password: choose your own before anything else. */
import React from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from './AuthProvider';
import { PasswordForm } from './PasswordForm';

export const ChangePasswordPage: React.FC = () => {
  const { me, refresh, signOut } = useAuth();
  const navigate = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from || '/';
  if (!me) return <Navigate to="/signin" replace />;
  if (!me.must_change_password) return <Navigate to={from} replace />;
  return (
    <div className="gs-login">
      <div className="gs-login-panel">
        <div className="gs-logo"><i />GraySpace</div>
        <h3 style={{ margin: '12px 0 4px' }}>Choose a new password</h3>
        <div className="gs-muted" style={{ marginBottom: 12 }}>
          You signed in as <b>{me.user?.username}</b> with a one-time password. Choose your own to continue.
        </div>
        <PasswordForm submitLabel="Set password and continue" onChanged={async () => { await refresh(); navigate(from, { replace: true }); }} />
        <button className="gs-btn ghost" style={{ marginTop: 8 }} onClick={signOut}>Sign out</button>
      </div>
    </div>
  );
};
