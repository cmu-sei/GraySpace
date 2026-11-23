/**
 * The builder (Build until the base is live, then Configuration): Base → Enclaves → DNS →
 * Certificates → (Player, with the deployment's Player option) → Review & deploy. Each step edits
 * the draft or the chosen enclaves' specs; moving between steps saves them. Read-only without build
 * access.
 */
import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { canIn } from '../../auth/AuthProvider';
import { deploymentState, isLive } from '../../shell/deployments';
import { Deployment } from '../../types/control';
import { BaseStep } from './BaseStep';
import { CertificatesStep } from './CertificatesStep';
import { DnsStep } from './DnsStep';
import { EnclavesStep } from './EnclavesStep';
import { PlayerStep } from './PlayerStep';
import { ReviewStep } from './ReviewStep';
import { useBuilder } from './useBuilder';

export const STEPS = [
  { key: 'base', label: 'Core' },
  { key: 'enclaves', label: 'Enclaves' },
  { key: 'dns', label: 'DNS' },
  { key: 'certificates', label: 'Certificates' },
  { key: 'player', label: 'Player' },
  { key: 'review', label: 'Review & deploy' },
] as const;

/** The deployment's steps: Player only with its Player option. */
export const stepsFor = (deployment: Deployment) => STEPS.filter((s) => s.key !== 'player' || Boolean(deployment.options.player));

const Banner: React.FC<{ deployment: Deployment; canBuild: boolean }> = ({ deployment, canBuild }) => {
  const state = deploymentState(deployment);
  const job = deployment.summary?.active_job;
  const id = deployment.id;
  return (
    <>
      {state === 'none' && <div className="gs-banner warn">Nothing is deployed on {id} yet. Choose a core and enclaves, then deploy from Review.</div>}
      {state === 'deploying' && job && <div className="gs-banner info">⟳ {job.kind}{job.stage ? `: ${job.stage}` : ''}{job.attempt ? ` (${job.attempt})` : ''}. <Link to={`/d/${id}/jobs/${job.id}`}>Follow it</Link></div>}
      {state === 'failed' && <div className="gs-banner bad">The last core job failed. <Link to={`/d/${id}/jobs/${deployment.summary?.last_base_job?.id}`}>See why</Link>, or retry it from Jobs.</div>}
      {isLive(deployment) && <div className="gs-banner info">{id} is live. Core changes run a new deploy; adding or removing enclaves creates or destroys their instances. Review shows what would change first.</div>}
      {!canBuild && <div className="gs-banner warn">You can see the configuration but not change it: building needs build access.</div>}
    </>
  );
};

export const BuildWizard: React.FC<{ deployment: Deployment; step?: string }> = ({ deployment, step }) => {
  const navigate = useNavigate();
  const canBuild = canIn(deployment, 'build');
  const b = useBuilder(canBuild, Boolean(deployment.options.player));
  const live = isLive(deployment);
  const steps = stepsFor(deployment);
  const index = Math.max(0, steps.findIndex((s) => s.key === step));
  const current = steps[index].key;
  const go = async (i: number) => {
    if (b.dirty && canBuild && !(await b.save())) return;
    navigate(`/d/${deployment.id}/build/${steps[i].key}`);
  };
  const readOnly = !canBuild;

  return (
    <div className="gs-page gs-dark">
      <div className="gs-row" style={{ marginBottom: 8 }}>
        <div>
          <h2 className="gs-title">{live ? 'Configuration' : 'Build'}</h2>
          <div className="gs-subtitle" style={{ margin: 0 }}>
            <span className="gs-mono">{deployment.id}</span>{deployment.name !== deployment.id ? ` · ${deployment.name}` : ''}
            {b.savedAt && !b.dirty && <> · draft saved {new Date(b.savedAt).toLocaleString()}</>}
            {b.adopted && ' · the draft is what is deployed'}
          </div>
        </div>
        <span className="gs-spacer" />
        {canBuild && b.dirty && <button className="gs-btn ghost sm" onClick={() => b.save()} disabled={b.saving}>{b.saving ? 'Saving…' : 'Save'}</button>}
      </div>
      <Banner deployment={deployment} canBuild={canBuild} />
      {b.error && <div className="gs-banner bad">{b.error}</div>}
      <nav className="gs-steps" aria-label="Steps">
        {steps.map((s, i) => (
          <button key={s.key} className={i === index ? 'on' : i < index ? 'done' : ''} onClick={() => go(i)} aria-current={i === index ? 'step' : undefined}>
            <span className="n">{i < index ? '✓' : i + 1}</span>{s.label}
          </button>
        ))}
      </nav>
      {b.loading ? <div className="gs-empty">Loading the draft…</div> : (
        <>
          {current === 'base' && <BaseStep b={b} readOnly={readOnly} />}
          {current === 'enclaves' && <EnclavesStep b={b} readOnly={readOnly} />}
          {current === 'dns' && <DnsStep b={b} readOnly={readOnly} />}
          {current === 'certificates' && <CertificatesStep b={b} readOnly={readOnly} live={live} />}
          {current === 'player' && <PlayerStep b={b} readOnly={readOnly} />}
          {current === 'review' && <ReviewStep b={b} deploymentId={deployment.id} canBuild={canBuild} live={live} />}
          <div className="gs-footer">
            {index > 0 && <button className="gs-btn ghost" onClick={() => go(index - 1)}>Back</button>}
            <span className="gs-spacer" />
            {index < steps.length - 1 && <button className="gs-btn" onClick={() => go(index + 1)} disabled={b.saving}>Next: {steps[index + 1].label}</button>}
          </div>
        </>
      )}
    </div>
  );
};
