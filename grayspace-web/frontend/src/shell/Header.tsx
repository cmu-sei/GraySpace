/**
 * The header: the app switcher, the logo (home), the deployment switcher and the deployment's tabs, and the user
 * menu. Before a deployment is live only Build shows; the operate tabs appear once it is.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Link, NavLink, useMatch, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { Deployment } from '../types/control';
import { deploymentState, isLive, STATE_LABELS } from './deployments';
import { useDeployments } from './DeploymentsProvider';
import { landing, OPERATE_SECTIONS } from './sections';
import { ADMIN_TABS } from '../pages/admin/tabs';
import { AppSwitcher } from './AppSwitcher';

const useOutsideClose = (open: boolean, close: () => void) => {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open, close]);
  return ref;
};

export const StateDot: React.FC<{ deployment: Deployment }> = ({ deployment }) => (
  <span className={`gs-dot ${STATE_LABELS[deploymentState(deployment)][0]}`} title={STATE_LABELS[deploymentState(deployment)][1]} />
);

const Switcher: React.FC<{ current: Deployment }> = ({ current }) => {
  const { deployments } = useDeployments();
  const { can } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useOutsideClose(open, () => setOpen(false));
  const go = (d: Deployment) => {
    setOpen(false);
    navigate(`/d/${d.id}/${landing(isLive(d))}`);
  };
  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button className="gs-switcher" onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open}>
        <StateDot deployment={current} />
        <span className="gs-mono">{current.id}</span>
        {current.name !== current.id && <span className="gs-muted">{current.name}</span>}
        <span className="gs-muted">▾</span>
      </button>
      {open && (
        <div className="gs-menu" role="menu">
          {deployments.map((d) => (
            <button key={d.id} role="menuitem" onClick={() => go(d)}>
              <StateDot deployment={d} />
              <span className="gs-mono">{d.id}</span>
              <span>{d.name !== d.id ? d.name : ''}</span>
              <span className="gs-menu-meta">{STATE_LABELS[deploymentState(d)][1]}</span>
            </button>
          ))}
          <div className="gs-menu-sep" />
          <Link to="/" onClick={() => setOpen(false)}>All deployments…</Link>
          {can('deployments:create') && <Link to="/?new=1" onClick={() => setOpen(false)}>＋ New deployment</Link>}
        </div>
      )}
    </div>
  );
};

const UserMenu: React.FC = () => {
  const { me, signOut } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useOutsideClose(open, () => setOpen(false));
  if (!me) return null;
  const name = me.user?.name || me.user?.username || 'Service';
  const initials = name.split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join('') || '?';
  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button className="gs-avatar" onClick={() => setOpen(!open)} title={name} aria-haspopup="menu" aria-expanded={open}>{initials}</button>
      {open && (
        <div className="gs-menu right" role="menu" style={{ minWidth: 220 }}>
          <div className="gs-menu-head">
            <b>{name}</b>
            {me.user?.email && <div className="gs-muted">{me.user.email}</div>}
            <div style={{ marginTop: 4 }}><span className="gs-pill info">{me.role}</span> <span className="gs-muted" style={{ fontSize: 11 }}>{me.kind === 'service' ? 'service token' : 'local account'}</span></div>
          </div>
          <div className="gs-menu-sep" />
          {me.kind === 'user' && <Link to="/account" onClick={() => setOpen(false)}>Password &amp; sessions</Link>}
          {ADMIN_TABS.some((t) => me.permissions.includes(t.permission)) && (
            <>
              <div className="gs-menu-sep" />
              <div className="gs-muted" style={{ fontSize: 10.5, padding: '2px 8px', textTransform: 'uppercase', letterSpacing: '.06em' }}>Admin</div>
              {ADMIN_TABS.filter((t) => me.permissions.includes(t.permission)).map((t) => (
                <Link key={t.key} to={`/admin/${t.key}`} onClick={() => setOpen(false)}>{t.label}</Link>
              ))}
            </>
          )}
          <button onClick={async () => { setOpen(false); navigate('/signin', { replace: true }); await signOut(); }}>Sign out</button>
        </div>
      )}
    </div>
  );
};

export const Header: React.FC = () => {
  const match = useMatch('/d/:dep/*');
  const { find } = useDeployments();
  const current = find(match?.params.dep);
  const live = isLive(current);
  return (
    <header className="gs-header">
      <AppSwitcher />
      <Link className="brand" to="/"><span className="mark" aria-hidden="true" />GraySpace</Link>
      {current && <Switcher current={current} />}
      {current && (
        <nav className="gs-tabs">
          {live && OPERATE_SECTIONS.map((s) => <NavLink key={s.key} to={`/d/${current.id}/${s.key}`}>{s.label}</NavLink>)}
          {live && <span className="gs-sep" />}
          <NavLink to={`/d/${current.id}/build`}>{live ? 'Configuration' : 'Build'}</NavLink>
          <NavLink to={`/d/${current.id}/jobs`}>
            Jobs{current.summary?.active_job && <span className="gs-badge" title={`${current.summary.active_job.kind}: ${current.summary.active_job.stage ?? 'queued'}`}>⟳</span>}
          </NavLink>
          {current.my_access.includes('members:manage') && <><span className="gs-sep" /><NavLink to={`/d/${current.id}/settings`}>Settings</NavLink></>}
        </nav>
      )}
      <span className="gs-spacer" />
      <UserMenu />
    </header>
  );
};
