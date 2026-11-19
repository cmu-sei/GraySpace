/**
 * The deployments the signed-in user can see (GET /api/v1/deployments), kept current by the
 * deployments events websocket (/api/v1/deployments/events). The list is filtered by the server.
 */
import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { control } from '../api/control';
import { Deployment, DeploymentEvent } from '../types/control';

interface Deployments {
  deployments: Deployment[];
  loaded: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  find: (id: string | undefined) => Deployment | undefined;
}

const Context = createContext<Deployments | null>(null);

export const DeploymentsProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDeployments(await control.deployments());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    refresh();
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let closed = false;
    const connect = () => {
      ws = control.deploymentEvents();
      ws.onopen = () => {
        attempt = 0;
      };
      ws.onmessage = (message) => {
        let event: DeploymentEvent;
        try {
          event = JSON.parse(message.data);
        } catch {
          return;
        }
        if (event.type === 'deployment.deleted') {
          setDeployments((list) => list.filter((d) => d.id !== event.id));
        } else if (event.deployment) {
          const next = event.deployment;
          setDeployments((list) => (list.some((d) => d.id === next.id) ? list.map((d) => (d.id === next.id ? next : d)) : [...list, next]));
        }
      };
      ws.onclose = () => {
        if (closed) return;
        timer = setTimeout(connect, Math.min(1000 * 2 ** attempt++, 30000));
      };
    };
    connect();
    // Summaries (jobs, instances) change without an event; look again now and then
    const poll = setInterval(refresh, 15000);
    return () => {
      closed = true;
      clearInterval(poll);
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, [refresh]);

  const find = useCallback((id: string | undefined) => deployments.find((d) => d.id === id), [deployments]);

  return <Context.Provider value={{ deployments, loaded, error, refresh, find }}>{children}</Context.Provider>;
};

export const useDeployments = (): Deployments => {
  const value = useContext(Context);
  if (!value) throw new Error('useDeployments needs a DeploymentsProvider');
  return value;
};
