/**
 * The app switcher, before the logo: links to the other apps (Scenario Builder,
 * Chronicle, ...) from the server's list (GET /api/apps), each opening in a new window. It opens on hover and closes 200ms after
 * the pointer leaves; a click pins it open until a click or focus outside, or Escape.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { control } from '../api/control';
import { SwitcherApp } from '../types/control';

/** This app's ID in the list, which the switcher leaves out */
export const SELF_APP_ID = 'grayspace';
const CLOSE_DELAY = 200;

const DotsIcon: React.FC = () => (
  <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true" focusable="false">
    {[2, 6, 10].flatMap((cy) => [2, 6, 10].map((cx) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="1.1" />))}
  </svg>
);

export const AppSwitcher: React.FC = () => {
  const [apps, setApps] = useState<SwitcherApp[]>([]);
  const [hovered, setHovered] = useState(false);
  const [pinned, setPinned] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const open = hovered || pinned;

  useEffect(() => {
    let current = true;
    control.apps().then(
      (list) => current && setApps(Array.isArray(list) ? list : []),
      () => current && setApps([]),
    );
    return () => { current = false; };
  }, []);

  const cancelClose = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);
  useEffect(() => cancelClose, [cancelClose]);

  const close = useCallback(() => {
    cancelClose();
    setHovered(false);
    setPinned(false);
  }, [cancelClose]);

  useEffect(() => {
    if (!open) return;
    const outside = (e: Event) => {
      if (root.current && !root.current.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      close();
      trigger.current?.focus();
    };
    document.addEventListener('mousedown', outside);
    document.addEventListener('focusin', outside);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', outside);
      document.removeEventListener('focusin', outside);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, close]);

  const others = apps.filter((a) => a.id !== SELF_APP_ID && a.url);
  if (others.length === 0) return null;

  const enter = () => {
    cancelClose();
    setHovered(true);
  };
  const leave = () => {
    cancelClose();
    timer.current = setTimeout(() => {
      timer.current = null;
      setHovered(false);
    }, CLOSE_DELAY);
  };
  // Unpinning closes it too, so a second click reads as a toggle even with the pointer still on it
  const toggle = () => {
    cancelClose();
    if (pinned) close();
    else setPinned(true);
  };

  return (
    <div ref={root} className="gs-apps" onMouseEnter={enter} onMouseLeave={leave}>
      <button ref={trigger} type="button" className="gs-apps-trigger" onClick={toggle}
        aria-label="Apps" title="Apps" aria-haspopup="true" aria-expanded={open}>
        <DotsIcon />
      </button>
      {open && (
        <nav className="gs-apps-menu" aria-label="Apps">
          <div className="gs-apps-head">Apps</div>
          {others.map((a) => (
            <a key={a.id} href={a.url} target="_blank" rel="noopener noreferrer" onClick={close}>
              <span className="gs-apps-badge" aria-hidden="true">{a.name.charAt(0).toUpperCase()}</span>
              <span className="gs-apps-name">{a.name}</span>
            </a>
          ))}
        </nav>
      )}
    </div>
  );
};
