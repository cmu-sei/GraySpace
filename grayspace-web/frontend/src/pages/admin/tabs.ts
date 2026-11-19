/** The admin area's pages, each with the server-wide permission it needs. */
export const ADMIN_TABS = [
  { key: 'platform', label: 'Platform', permission: 'platform:manage' },
  { key: 'users', label: 'Users & roles', permission: 'users:manage' },
  { key: 'keys', label: 'API keys', permission: 'keys:manage' },
  { key: 'catalog', label: 'Images & roles', permission: 'catalog:write' },
  { key: 'audit', label: 'Audit log', permission: 'audit:read' },
] as const;
