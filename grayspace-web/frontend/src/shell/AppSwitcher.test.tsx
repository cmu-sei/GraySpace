import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { fakeServer } from '../testing';
import { AppSwitcher } from './AppSwitcher';

const APPS = [
  { id: 'scenario-builder', name: 'Scenario Builder', url: 'http://localhost:4310' },
  { id: 'grayspace', name: 'GraySpace', url: 'http://localhost:3000' },
  { id: 'chronicle', name: 'Chronicle', url: 'http://localhost:4320' },
  { id: 'ghosts', name: 'GHOSTS' },
];

const serve = (apps: unknown, status = 200) =>
  fakeServer((url) => (url.endsWith('/api/apps') ? { status, body: apps } : undefined));

afterEach(() => jest.useRealTimers());

describe('the app switcher', () => {
  it('lists the other apps that have a URL', async () => {
    const calls = serve(APPS);
    render(<AppSwitcher />);
    const trigger = await screen.findByRole('button', { name: 'Apps' });
    expect(calls.filter((c) => c.url.endsWith('/api/apps'))).toHaveLength(1);
    expect(trigger).toHaveAttribute('aria-haspopup', 'true');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');

    fireEvent.mouseEnter(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    const menu = screen.getByRole('navigation', { name: 'Apps' });
    const links = Array.from(menu.querySelectorAll('a'));
    expect(links.map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
      ['SScenario Builder', 'http://localhost:4310'],
      ['CChronicle', 'http://localhost:4320'],
    ]);
    expect(links.every((a) => a.getAttribute('target') === '_blank')).toBe(true);
    expect(links.every((a) => a.getAttribute('rel') === 'noopener noreferrer')).toBe(true);
    expect(screen.queryByText('GraySpace')).not.toBeInTheDocument();
    expect(screen.queryByText('GHOSTS')).not.toBeInTheDocument();
  });

  it('is hidden when no other app has a URL', async () => {
    const calls = serve([{ id: 'grayspace', name: 'GraySpace', url: 'http://localhost:3000' }, { id: 'ghosts', name: 'GHOSTS' }]);
    const { container } = render(<AppSwitcher />);
    await act(async () => { await Promise.resolve(); });
    expect(calls).toHaveLength(1);
    expect(container).toBeEmptyDOMElement();
  });

  it('is hidden when the list cannot be read', async () => {
    const calls = serve({ detail: 'boom' }, 500);
    const { container } = render(<AppSwitcher />);
    await act(async () => { await Promise.resolve(); });
    expect(calls).toHaveLength(1);
    expect(container).toBeEmptyDOMElement();
  });

  it('closes 200ms after the pointer leaves, unless it comes back', async () => {
    serve(APPS);
    render(<AppSwitcher />);
    const trigger = await screen.findByRole('button', { name: 'Apps' });
    jest.useFakeTimers();
    fireEvent.mouseEnter(trigger);
    fireEvent.mouseLeave(trigger);
    act(() => { jest.advanceTimersByTime(150); });
    fireEvent.mouseEnter(screen.getByRole('navigation', { name: 'Apps' }));
    act(() => { jest.advanceTimersByTime(500); });
    expect(trigger).toHaveAttribute('aria-expanded', 'true');

    fireEvent.mouseLeave(trigger);
    act(() => { jest.advanceTimersByTime(199); });
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    act(() => { jest.advanceTimersByTime(1); });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
  });

  it('stays open after a click when the pointer leaves, and Escape closes it', async () => {
    serve(APPS);
    render(<AppSwitcher />);
    const trigger = await screen.findByRole('button', { name: 'Apps' });
    jest.useFakeTimers();
    fireEvent.mouseEnter(trigger);
    fireEvent.click(trigger);
    fireEvent.mouseLeave(trigger);
    act(() => { jest.advanceTimersByTime(1000); });
    expect(screen.getByRole('navigation', { name: 'Apps' })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(trigger).toHaveFocus();
  });

  it('closes on a click or focus outside', async () => {
    serve(APPS);
    render(<><AppSwitcher /><button>elsewhere</button></>);
    const trigger = await screen.findByRole('button', { name: 'Apps' });
    fireEvent.click(trigger);
    expect(screen.getByRole('navigation', { name: 'Apps' })).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();

    fireEvent.click(trigger);
    act(() => { screen.getByRole('button', { name: 'elsewhere' }).focus(); });
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
  });
});
