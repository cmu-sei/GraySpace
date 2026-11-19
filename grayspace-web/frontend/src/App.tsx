/**
 * The routes: sign-in, then the deployments home, a deployment (/d/:dep/:section) and your account
 * (docs/deployments.md for the server side).
 */
import React, { Suspense } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth/AuthProvider';
import { ChangePasswordPage } from './auth/ChangePasswordPage';
import { RequireAuth } from './auth/RequireAuth';
import { SignInPage } from './auth/SignInPage';
import { AccountPage } from './pages/account/AccountPage';
import { DeploymentsHome } from './pages/home/DeploymentsHome';
import { AppShell, BareShell } from './shell/AppShell';
import { DeploymentShell } from './shell/DeploymentShell';
import { DeploymentsProvider } from './shell/DeploymentsProvider';
import { ROUTER_FUTURE } from './shell/router';
import { lazyNamed } from './utils/lazy';
import './theme.css';

const AdminPage = lazyNamed(() => import('./pages/admin/AdminPage'), 'AdminPage');
const loadingPage = <div className="gs-empty">Loading…</div>;

export const AppRoutes: React.FC = () => (
  <Routes>
    <Route element={<BareShell />}>
      <Route path="/signin" element={<SignInPage />} />
      <Route path="/signin/change-password" element={<ChangePasswordPage />} />
    </Route>
    <Route element={<RequireAuth><DeploymentsProvider><AppShell /></DeploymentsProvider></RequireAuth>}>
      <Route index element={<DeploymentsHome />} />
      <Route path="account" element={<AccountPage />} />
      <Route path="admin" element={<Suspense fallback={loadingPage}><AdminPage /></Suspense>} />
      <Route path="admin/:tab" element={<Suspense fallback={loadingPage}><AdminPage /></Suspense>} />
      {/* One route, so moving between a deployment's views never remounts it */}
      <Route path="d/:dep/*" element={<DeploymentShell />} />
    </Route>
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes>
);

const App: React.FC = () => (
  <BrowserRouter future={ROUTER_FUTURE}>
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  </BrowserRouter>
);

export default App;
