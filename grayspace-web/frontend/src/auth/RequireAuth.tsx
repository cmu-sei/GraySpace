import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from './AuthProvider';

/** Signed in, with a password of their own; otherwise to sign-in (and back here after). */
export const RequireAuth: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="gs-login"><span className="gs-muted">Loading…</span></div>;
  if (!me) return <Navigate to="/signin" replace state={{ from: location.pathname + location.search }} />;
  if (me.must_change_password) return <Navigate to="/signin/change-password" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
};
