/**
 * One deployment: /d/:dep/:section[/:item] (item: a job on the Jobs page). Points the API at the deployment and forgets the last one's
 * topology before anything mounts, and remounts the workspace when the deployment changes, so its
 * map, websockets and store start fresh. Operate views need a live base; until there is one they
 * lead to Build.
 */
import React, { Suspense, useLayoutEffect, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { apiService } from '../services/api';
import { useTopologyStore } from '../stores/topologyStore';
import { isLive } from './deployments';
import { useDeployments } from './DeploymentsProvider';
import { isOperateSection, landing, Section, SECTIONS } from './sections';
import { lazyNamed } from '../utils/lazy';

// A chunk of its own, so the home page doesn't load the deployment views
const Workspace = lazyNamed(() => import('../pages/workspace/Workspace'), 'Workspace');

export const DeploymentShell: React.FC = () => {
  const { dep, '*': rest = '' } = useParams();
  const [section, item] = rest.split('/');
  const { find, loaded } = useDeployments();
  const deployment = find(dep);
  // The deployment the API points at; the workspace renders only once it is this one
  const [opened, setOpened] = useState<string | null>(apiService.deployment);
  const id = deployment?.id;
  useLayoutEffect(() => {
    if (id && apiService.deployment !== id) {
      useTopologyStore.getState().reset();
      apiService.setDeployment(id);
    }
    if (id) setOpened(id);
  }, [id]);

  if (!deployment) {
    if (!loaded) return <div className="gs-empty">Loading…</div>;
    return (
      <div className="gs-page"><div className="gs-empty">
        No deployment <span className="gs-mono">{dep}</span>, or it isn't yours to see. <Link to="/">All deployments</Link>
      </div></div>
    );
  }
  const live = isLive(deployment);
  if (!section || !SECTIONS.includes(section as Section)) return <Navigate to={`/d/${deployment.id}/${landing(live)}`} replace />;
  if (isOperateSection(section) && !live) return <Navigate to={`/d/${deployment.id}/build`} replace />;
  if (section === 'settings' && !deployment.my_access.includes('members:manage')) return <Navigate to={`/d/${deployment.id}/${landing(live)}`} replace />;
  if (deployment.worker.state !== 'running') {
    return (
      <div className="gs-page"><div className="gs-banner warn">
        {deployment.id}'s backend is {deployment.worker.state}{deployment.worker.error ? `: ${deployment.worker.error}` : ''}. It restarts on its own; this page follows.
      </div></div>
    );
  }
  if (opened !== deployment.id || apiService.deployment !== deployment.id) return null;
  return (
    <Suspense fallback={<div className="gs-empty">Loading {deployment.id}…</div>}>
      <Workspace key={deployment.id} deployment={deployment} section={section as Section} item={item} />
    </Suspense>
  );
};
