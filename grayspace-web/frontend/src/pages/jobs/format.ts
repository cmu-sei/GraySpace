/** How jobs read on the Jobs page and the Overview. */
import { JobRecord } from '../../types/slices';

export const FINISHED = ['succeeded', 'failed', 'cancelled'];

export const isFinished = (job: Pick<JobRecord, 'state'>): boolean => FINISHED.includes(job.state);

export const STATE_TONE: Record<string, string> = { queued: '', running: 'info', succeeded: 'ok', failed: 'bad', cancelled: 'warn' };

export const KIND_LABELS: Record<string, string> = {
  rollout: 'Deploy',
  deploy_base: 'Deploy core',
  configure_base: 'Configure core',
  destroy_base: 'Destroy core',
  create_instance: 'Create instance',
  destroy_instance: 'Destroy instance',
  plan_slice: 'Plan enclave',
};

export const kindLabel = (kind: string): string => KIND_LABELS[kind] || kind;

// Terminal color codes, which Terraform writes; stripped from the whole log, as a stream chunk can end mid-code
// eslint-disable-next-line no-control-regex
const ANSI = /\x1b\[[0-9;]*[A-Za-z]/g;

export const stripAnsi = (text: string): string => text.replace(ANSI, '');

export const ago = (iso: string | null | undefined): string => {
  if (!iso) return '';
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleString();
};

export const duration = (job: JobRecord): string => {
  if (!job.started) return '';
  const end = job.finished ? new Date(job.finished).getTime() : Date.now();
  const s = Math.max(0, Math.round((end - new Date(job.started).getTime()) / 1000));
  return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
};

export type StageStatus = 'done' | 'current' | 'failed' | 'skipped' | 'pending';

/** Each stage's status: those before a retry's resume point were done by the job it retried. */
export const stageStatuses = (job: JobRecord): StageStatus[] => {
  const at = job.stage ? job.stages.indexOf(job.stage) : -1;
  const resume = job.resume_from ? job.stages.indexOf(job.resume_from) : -1;
  return job.stages.map((_, i) => {
    if (job.state === 'succeeded') return i < resume ? 'skipped' : 'done';
    if (i < resume && (at < 0 || i < at)) return 'skipped';
    if (at < 0) return 'pending';
    if (i < at) return 'done';
    if (i === at) return job.state === 'failed' || job.state === 'cancelled' ? 'failed' : job.state === 'running' ? 'current' : 'pending';
    return 'pending';
  });
};

export const stageTitle = (job: JobRecord, stage: string): string => job.stage_titles?.[stage] || stage;

/** Where a job is, in a line: "Step 2 of 4: Create the routers and services", "Failed: …", "Done". */
export const progressText = (job: JobRecord): string => {
  const statuses = stageStatuses(job);
  const at = statuses.findIndex((s) => s === 'current' || s === 'failed');
  if (job.state === 'succeeded') return 'Done';
  if (job.state === 'queued') return 'Waiting to start';
  if (at < 0) return job.state === 'cancelled' ? 'Cancelled' : '';
  const title = stageTitle(job, job.stages[at]);
  if (statuses[at] === 'failed') return `${job.state === 'cancelled' ? 'Cancelled' : 'Failed'}: ${title}`;
  return `Step ${at + 1} of ${job.stages.length}: ${title}`;
};

/** The share of a job's stages that are done (or were, before a retry). */
export const progressShare = (job: JobRecord): number => {
  if (!job.stages.length) return job.state === 'succeeded' ? 1 : 0;
  return stageStatuses(job).filter((s) => s === 'done' || s === 'skipped').length / job.stages.length;
};
