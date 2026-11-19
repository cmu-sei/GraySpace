/** A deployment's views: the operate ones need a live base; Build (Configuration once live) and Jobs don't. */
export const OPERATE_SECTIONS = [
  { key: 'overview', label: 'Overview' },
  { key: 'map', label: 'Map' },
  { key: 'enclaves', label: 'Enclaves' },
  { key: 'dns', label: 'DNS' },
  { key: 'certificates', label: 'Certificates' },
] as const;

export type Section = (typeof OPERATE_SECTIONS)[number]['key'] | 'build' | 'jobs' | 'settings';

export const SECTIONS: Section[] = [...OPERATE_SECTIONS.map((s) => s.key), 'build', 'jobs', 'settings'];

export const isOperateSection = (section: string | undefined): boolean => OPERATE_SECTIONS.some((s) => s.key === section);

/** Where opening a deployment lands: its overview when it is live, else the builder. */
export const landing = (live: boolean): Section => (live ? 'overview' : 'build');
