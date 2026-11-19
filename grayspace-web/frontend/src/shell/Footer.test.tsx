import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AppRoutes } from '../App';
import { AuthProvider } from '../auth/AuthProvider';
import { clearToken, setToken } from '../services/auth';
import { fakeServer, me, SilentWebSocket } from '../testing';
import { Footer, REPOSITORY_URL } from './Footer';
import { ROUTER_FUTURE } from './router';

beforeEach(() => {
  (global as unknown as { WebSocket: unknown }).WebSocket = SilentWebSocket;
  clearToken();
});

const notices = (footer: HTMLElement) => Array.from(footer.querySelectorAll('.notice'));

describe('the footer', () => {
  it('names the product at the left and the copyright at the right', () => {
    render(<Footer />);
    const [left, right] = notices(screen.getByRole('contentinfo'));
    expect(left).toHaveTextContent(/^GraySpace\.$/);
    expect(right).toHaveTextContent(/^CERT © 2022\. All rights reserved\.$/);
  });

  it('has one link, to the repository, drawn as the GitHub mark', () => {
    render(<Footer />);
    const links = within(screen.getByRole('contentinfo')).getAllByRole('link');
    expect(links).toHaveLength(1);
    const [github] = links;
    expect(github).toHaveAttribute('href', REPOSITORY_URL);
    expect(github).toHaveAttribute('aria-label', 'GraySpace on GitHub');
    expect(github).toHaveAttribute('target', '_blank');
    expect(github.querySelector('svg')).not.toBeNull();
    expect(github.textContent).toBe('');
  });

  it('asks the server nothing: GraySpace has no release number yet', () => {
    const calls = fakeServer(() => undefined);
    render(<Footer />);
    expect(calls).toHaveLength(0);
  });

  const at = (path: string, signedIn: boolean) => {
    if (signedIn) setToken('gss_t');
    fakeServer((url) => {
      if (url.endsWith('/api/v1/me')) return signedIn ? { body: me('builder') } : { status: 401, body: {} };
      if (url.endsWith('/api/v1/auth/providers')) return { body: { providers: ['local'] } };
      return { body: [] };
    });
    return render(<MemoryRouter future={ROUTER_FUTURE} initialEntries={[path]}><AuthProvider><AppRoutes /></AuthProvider></MemoryRouter>);
  };

  it('closes every page, after the routed page', async () => {
    const { container } = at('/', true);
    await screen.findByRole('banner');
    const frame = container.querySelector('.gs-app')!;
    expect(Array.from(frame.children).map((c) => c.className)).toEqual(['gs-header', 'gs-routed', 'gs-appfooter']);
  });

  it('is on the sign-in page too', async () => {
    const { container } = at('/signin', false);
    await screen.findByLabelText('Username');
    const frame = container.querySelector('.gs-app')!;
    expect(Array.from(frame.children).map((c) => c.className)).toEqual(['gs-routed', 'gs-appfooter']);
  });
});
