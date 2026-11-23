import { deployment } from '../testing';
import { pageTitle } from './title';

describe('pageTitle', () => {
  const live = deployment('dev', { name: 'Exercise', core: { status: 'healthy' } });
  const draft = deployment('new');
  const find = (id?: string) => [live, draft].find((d) => d.id === id);

  it('names the page, then the deployment, then GraySpace', () => {
    expect(pageTitle('/', find)).toBe('Deployments · GraySpace');
    expect(pageTitle('/signin')).toBe('Sign in · GraySpace');
    expect(pageTitle('/admin/catalog', find)).toBe('Images & roles · Admin · GraySpace');
    expect(pageTitle('/d/dev/map', find)).toBe('Map · Exercise · GraySpace');
    expect(pageTitle('/d/dev/jobs/abc', find)).toBe('Jobs · Exercise · GraySpace');
  });

  it('calls Build Configuration once the deployment is live, and falls back to the ID', () => {
    expect(pageTitle('/d/dev/build', find)).toBe('Configuration · Exercise · GraySpace');
    expect(pageTitle('/d/new/build', find)).toBe(`Build · ${draft.name} · GraySpace`);
    expect(pageTitle('/d/gone/overview', find)).toBe('Overview · gone · GraySpace');
  });
});
