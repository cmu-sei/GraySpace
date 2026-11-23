/** The browser tab's title: the page, then the deployment, then GraySpace ("Map · dev · GraySpace"). */
import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { ADMIN_TABS } from '../pages/admin/tabs';
import { Deployment } from '../types/control';
import { isLive } from './deployments';
import { OPERATE_SECTIONS } from './sections';

const APP = 'GraySpace';

const sectionLabel = (section: string, deployment: Deployment | undefined): string | undefined => {
  if (section === 'build') return isLive(deployment) ? 'Configuration' : 'Build';
  if (section === 'jobs') return 'Jobs';
  if (section === 'settings') return 'Settings';
  return OPERATE_SECTIONS.find((s) => s.key === section)?.label;
};

export const pageTitle = (pathname: string, find: (id?: string) => Deployment | undefined = () => undefined): string => {
  const parts = pathname.split('/').filter(Boolean).map(decodeURIComponent);
  const join = (...p: (string | undefined)[]) => [...p.filter(Boolean), APP].join(' · ');
  switch (parts[0]) {
    case undefined: return join('Deployments');
    case 'signin': return join(parts[1] === 'change-password' ? 'Change password' : 'Sign in');
    case 'account': return join('Account');
    case 'admin': return join(ADMIN_TABS.find((t) => t.key === parts[1])?.label, 'Admin');
    case 'd': {
      const deployment = find(parts[1]);
      return join(parts[2] && sectionLabel(parts[2], deployment), deployment?.name || parts[1]);
    }
    default: return APP;
  }
};

export const useDocumentTitle = (find?: (id?: string) => Deployment | undefined): void => {
  const { pathname } = useLocation();
  const title = pageTitle(pathname, find);
  useEffect(() => {
    document.title = title;
  }, [title]);
};
