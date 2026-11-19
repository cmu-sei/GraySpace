/**
 * The admin area, from the user menu: it applies to the whole GraySpace server, every deployment
 * it runs. The platform's Proxmox and Crucible connections, users and roles, API keys, the image and
 * role catalog, the audit log. The Proxmox nodes are on Platform.
 */
import React from 'react';
import { Navigate, NavLink, useParams } from 'react-router-dom';
import { useAuth } from '../../auth/AuthProvider';
import { ApiKeysPage } from './ApiKeysPage';
import { AuditPage } from './AuditPage';
import { CatalogPage } from './CatalogPage';
import { PlatformPage } from './PlatformPage';
import { ADMIN_TABS } from './tabs';
import { UsersPage } from './UsersPage';

export const AdminPage: React.FC = () => {
  const { tab } = useParams();
  const { can } = useAuth();
  const allowed = ADMIN_TABS.filter((t) => can(t.permission));
  if (!allowed.length) return <div className="gs-page gs-dark"><div className="gs-empty">The admin area is for admins.</div></div>;
  const current = allowed.find((t) => t.key === tab);
  if (!current) return <Navigate to={`/admin/${allowed[0].key}`} replace />;
  return (
    <div className="gs-page gs-dark">
      <h2 className="gs-title">Admin</h2>
      <nav className="gs-tabs gs-subtabs">
        {allowed.map((t) => <NavLink key={t.key} to={`/admin/${t.key}`}>{t.label}</NavLink>)}
      </nav>
      {current.key === 'platform' && <PlatformPage />}
      {current.key === 'users' && <UsersPage />}
      {current.key === 'keys' && <ApiKeysPage />}
      {current.key === 'catalog' && <CatalogPage />}
      {current.key === 'audit' && <AuditPage />}
    </div>
  );
};
