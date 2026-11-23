/**
 * Step 5: what deploying would change (GET /deploy/plan), the node's headroom, and Deploy (or Apply
 * changes): one rollout job, shown as it runs, step by step.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { apiService } from '../../services/api';
import { useDeployments } from '../../shell/DeploymentsProvider';
import { DeployPlan } from '../../types/build';
import { Tip } from '../../components/Tip';
import { JobRecord } from '../../types/slices';
import { isFinished } from '../jobs/format';
import { JobDetail } from '../jobs/JobDetail';
import { Bar } from './parts';
import { Builder } from './useBuilder';

const PLAYER: Record<string, [string, string]> = { create: ['info', '+ create'], update: ['warn', 'update'], destroy: ['bad', '− remove'], none: ['', 'no change'] };

const ACTION: Record<string, [string, string]> = {
  create: ['info', '+ create'], destroy: ['bad', '− destroy'], replace: ['warn', '↻ replace'], keep: ['', 'keep'],
  deploy: ['info', 'deploy'], update: ['warn', 'update'], none: ['', 'no change'],
};

export const ReviewStep: React.FC<{ b: Builder; deploymentId: string; canBuild: boolean; live: boolean }> = ({ b, deploymentId, canBuild, live: listedLive }) => {
  const { refresh } = useDeployments();
  const [plan, setPlan] = useState<DeployPlan | null>(null);
  const [rollout, setRollout] = useState<JobRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, latest] = await Promise.all([apiService.getDeployPlan(), apiService.getJobs({ kind: ['rollout'], limit: 1 })]);
      setPlan(p);
      setRollout(latest[0] || null);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);
  // Opened directly (not by Next), the draft may have changes; the plan is the saved draft's
  useEffect(() => {
    if (b.dirty && canBuild && !b.saving) b.save();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!b.dirty) load();
  }, [b.dirty, load]);

  // Follow the rollout until it ends, then look again at what is left to change
  useEffect(() => {
    if (!rollout || isFinished(rollout)) return;
    const timer = setInterval(async () => {
      const current = await apiService.getJob(rollout.id).catch(() => null);
      if (current && isFinished(current)) {
        clearInterval(timer);
        await Promise.all([load(), refresh()]);
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [rollout, load, refresh]);

  const deploy = async () => {
    setBusy(true);
    setError(null);
    try {
      if (b.dirty && !(await b.save())) return;
      const accepted = await apiService.deploy();
      setRollout(accepted.job);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const running = rollout && !isFinished(rollout);
  // The plan is the worker's answer now; the deployments list can lag a rollout that just ended
  const live = plan ? Boolean(plan.core.deployed) : listedLive;
  const creates = plan?.instances.filter((i) => i.action === 'create' || i.action === 'replace').length ?? 0;
  const label = live ? 'Apply changes' : `Deploy the core${creates ? ` and ${creates} enclave${creates === 1 ? '' : 's'}` : ''}`;
  const cap = plan?.capacity;
  // A failed rollout carries out the plan of the draft it deployed: once the draft has changed, retrying it
  // can't help, so it isn't shown (it stays in Jobs). Only failures someone can act on are.
  const stale = Boolean(rollout && (rollout.state === 'failed' || rollout.state === 'cancelled') && plan && plan.resumable_rollout !== rollout.id);

  return (
    <div className="gs-grid2" style={{ gridTemplateColumns: 'minmax(0, 1.4fr) minmax(300px, 1fr)', alignItems: 'start' }}>
      <div>
        <div className="gs-box">
          <h3>What deploying changes</h3>
          {b.dirty && <div className="gs-banner warn">Unsaved changes: save them to see what deploying would do.</div>}
          {error && <div className="gs-banner bad">{error}</div>}
          {plan && (
            <table className="gs-table">
              <tbody>
                <tr>
                  <td style={{ width: 110 }}><span className={`gs-pill ${ACTION[plan.core.action][0]}`}>{ACTION[plan.core.action][1]}</span></td>
                  <td>Core {plan.core.version || plan.core.deployed?.name}{(plan.core.scope || plan.core.deployed?.scope) ? ` · ${plan.core.scope || plan.core.deployed?.scope}` : ''}</td>
                  <td className="gs-muted">
                    {plan.core.action === 'update' && plan.core.deployed ? `from ${plan.core.deployed.name} · ${plan.core.deployed.scope}` : plan.core.action !== 'none' && !plan.core.configure ? 'without configure' : ''}
                    {plan.stages.includes('preset') && <div>apply preset {plan.core.preset}{plan.core.active_preset && plan.core.active_preset !== plan.core.preset ? ` (now ${plan.core.active_preset})` : ''}</div>}
                  </td>
                </tr>
                {plan.player && (plan.player.action !== 'none' || plan.player.view) && (
                  <tr>
                    <td><span className={`gs-pill ${PLAYER[plan.player.action][0]}`}>{PLAYER[plan.player.action][1]}</span></td>
                    <td>Player view {plan.player.view && <span className="gs-mono">{plan.player.view}</span>}</td>
                    <td className="gs-muted">
                      {plan.player.action !== 'destroy' && `${plan.player.teams} team${plan.player.teams === 1 ? '' : 's'} · ${plan.player.guests} resource${plan.player.guests === 1 ? '' : 's'} chosen`}
                      {plan.player.warnings.length > 0 && (
                        <details>
                          <summary style={{ color: 'var(--gs-warn)', cursor: 'pointer' }}>{plan.player.warnings.length} warning{plan.player.warnings.length === 1 ? '' : 's'}</summary>
                          {plan.player.warnings.map((w) => <div key={w}>{w}</div>)}
                        </details>
                      )}
                    </td>
                  </tr>
                )}
                {plan.instances.map((i) => (
                  <tr key={i.id}>
                    <td><span className={`gs-pill ${ACTION[i.action][0]}`}>{ACTION[i.action][1]}</span></td>
                    <td><span className="gs-mono">{i.id}</span> · {i.slug}</td>
                    <td className="gs-muted gs-mono" style={{ fontSize: 10.5 }}>{i.version?.slice(0, 19)}{i.state ? ` · ${i.state}` : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {plan?.unmanaged.length ? <div className="gs-muted" style={{ marginTop: 8 }}>Not managed here (made outside the builder, e.g. by GHOSTS): {plan.unmanaged.join(', ')}</div> : null}
          {plan?.problems.map((p) => <div key={p} className="gs-finding bad">{p}</div>)}
          <div className="gs-footer">
            {canBuild ? (
              <button className="gs-btn" onClick={deploy} disabled={busy || Boolean(running) || !plan || (!plan.changes && !b.dirty) || plan.problems.length > 0}>
                {running ? 'Deploying…' : busy ? 'Starting…' : label}
              </button>
            ) : <span className="gs-muted">Deploying needs build access.</span>}
            {canBuild && <Tip text="Applies the plan above. A failed deploy can be retried from the failed step." />}
            {plan && !plan.changes && !b.dirty && <span className="gs-muted">Nothing to change.</span>}
          </div>
        </div>
        {rollout && !stale && <JobDetail key={rollout.id} summary deploymentId={deploymentId} jobId={rollout.id} canBuild={canBuild} onRetried={(id) => apiService.getJob(id).then(setRollout)} />}
      </div>
      <div className="gs-box">
        <h3>Capacity</h3>
        {cap ? (
          <>
            <Bar label={`CPU (${cap.cpus ?? '?'} cores)`} used={cap.cpu_usage} total={1} unit={() => ''} />
            <Bar label="Memory" used={cap.memory_total != null && cap.memory_free != null ? cap.memory_total - cap.memory_free : null} total={cap.memory_total} />
            <Bar label={`Storage${cap.storage ? ` (${cap.storage})` : ''}`} used={cap.storage_total != null && cap.storage_free != null ? cap.storage_total - cap.storage_free : null} total={cap.storage_total} />
          </>
        ) : <div className="gs-muted">Not available.</div>}
      </div>
    </div>
  );
};
