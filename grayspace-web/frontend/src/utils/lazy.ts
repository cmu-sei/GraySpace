/**
 * React.lazy for a named export, with preload() to fetch its chunk before it renders.
 */
import React from 'react';

export function lazyNamed<M, K extends keyof M>(load: () => Promise<M>, name: K) {
  let pending: Promise<M> | null = null;
  const preload = () => (pending ??= load());
  const Component = React.lazy(() => preload().then((m) => ({ default: m[name] as unknown as React.ComponentType<any> })));
  return Object.assign(Component, { preload }) as unknown as M[K] & { preload: () => Promise<M> };
}
