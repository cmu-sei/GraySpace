/** What the switcher and the home page say about a deployment, from its record and summary. */
import { Deployment, DeploymentState } from '../types/control';

/** Live: a base is deployed (healthy, degraded, or not answering for its health). The operate views need one. */
export const isLive = (d: Deployment | null | undefined): boolean => Boolean(d?.core && d.core.status !== 'not_deployed');

export const deploymentState = (d: Deployment): DeploymentState => {
  if (d.worker.state !== 'running') return 'down';
  const summary = d.summary;
  if (summary?.active_job && ['rollout', 'deploy_base', 'destroy_base'].includes(summary.active_job.kind)) return 'deploying';
  if (!isLive(d)) {
    if (summary?.last_core_job?.state === 'failed') return 'failed';
    return summary?.draft?.saved ? 'draft' : 'none';
  }
  return d.core?.status === 'degraded' ? 'degraded' : 'live';
};

export const STATE_LABELS: Record<DeploymentState, [string, string]> = {
  none: ['', 'Not deployed'],
  draft: ['info', 'Draft'],
  deploying: ['info', 'Deploying'],
  live: ['ok', 'Live'],
  degraded: ['warn', 'Degraded'],
  failed: ['bad', 'Failed'],
  down: ['bad', 'Worker down'],
};
