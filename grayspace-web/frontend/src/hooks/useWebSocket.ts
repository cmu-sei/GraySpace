/**
 * WebSocket hook for real-time state updates
 *
 * Maintains a persistent connection with automatic reconnection (exponential
 * backoff) and periodic keepalive pings to prevent idle timeouts.
 *
 * Handles browser tab freeze/suspend: when a tab is backgrounded for a long
 * time, browsers freeze JS execution and silently kill WebSocket connections.
 * The visibilitychange listener detects when the tab is reactivated and forces
 * a fresh reconnection if the connection has gone stale.
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import { apiService } from '../services/api';
import { useTopologyStore } from '../stores/topologyStore';
import { LinkStateUpdate } from '../types';

const PING_INTERVAL_MS = 30_000;
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;
/** If no message received for this long, consider the connection stale */
const STALE_THRESHOLD_MS = 90_000;

export const useWebSocket = () => {
  const wsRef = useRef<WebSocket | null>(null);
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptRef = useRef(0);
  const unmountedRef = useRef(false);
  const lastReceivedRef = useRef<number>(Date.now());
  const hasConnectedRef = useRef(false);

  const [connected, setConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<any>(null);

  const updateLinkState = useTopologyStore((state) => state.updateLinkState);
  const setInitialLinkStates = useTopologyStore((state) => state.setInitialLinkStates);
  const setRouters = useTopologyStore((state) => state.setRouters);
  const setLinks = useTopologyStore((state) => state.setLinks);
  const setISPs = useTopologyStore((state) => state.setISPs);
  const setDNSServers = useTopologyStore((state) => state.setDNSServers);

  const refreshTopology = useCallback(async () => {
    try {
      const [routers, links, isps, dns] = await Promise.all([
        apiService.getRouters(),
        apiService.getLinks(),
        apiService.getISPs(),
        apiService.getDNSServers(),
      ]);
      setRouters(routers);
      setLinks(links);
      setISPs(isps);
      setDNSServers(dns);
      console.log('Topology refreshed after reconnect');
    } catch (err) {
      console.error('Failed to refresh topology after reconnect:', err);
    }
  }, [setRouters, setLinks, setISPs, setDNSServers]);

  const clearPingInterval = useCallback(() => {
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = null;
    }
  }, []);

  const startPingInterval = useCallback(() => {
    clearPingInterval();
    pingIntervalRef.current = setInterval(() => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send('ping');
      }
    }, PING_INTERVAL_MS);
  }, [clearPingInterval]);

  const connect = useCallback(() => {
    if (unmountedRef.current) return;

    const ws = apiService.createWebSocket();
    wsRef.current = ws;

    ws.onopen = () => {
      const isReconnect = hasConnectedRef.current;
      hasConnectedRef.current = true;
      console.log(`WebSocket ${isReconnect ? 're' : ''}connected`);
      reconnectAttemptRef.current = 0;
      lastReceivedRef.current = Date.now();
      setConnected(true);
      startPingInterval();
      if (isReconnect) {
        refreshTopology();
      }
    };

    ws.onmessage = (event) => {
      lastReceivedRef.current = Date.now();
      try {
        const data = JSON.parse(event.data);
        setLastMessage(data);

        switch (data.type) {
          case 'link_state_update':
            updateLinkState(data as LinkStateUpdate);
            break;

          case 'initial_link_states':
            if (data.link_states) {
              setInitialLinkStates(data.link_states);
            }
            break;

          case 'pong':
            // Heartbeat response
            break;

          default:
            console.log('Unknown message type:', data);
        }
      } catch (error) {
        console.error('Error parsing WebSocket message:', error);
      }
    };

    ws.onerror = (error) => {
      console.error('WebSocket error:', error);
    };

    ws.onclose = () => {
      console.log('WebSocket disconnected');
      setConnected(false);
      clearPingInterval();

      if (unmountedRef.current) return;

      // Reconnect with exponential backoff
      const attempt = reconnectAttemptRef.current;
      const delay = Math.min(RECONNECT_BASE_MS * Math.pow(2, attempt), RECONNECT_MAX_MS);
      reconnectAttemptRef.current = attempt + 1;
      console.log(`WebSocket reconnecting in ${delay}ms (attempt ${attempt + 1})`);
      reconnectTimeoutRef.current = setTimeout(connect, delay);
    };
  }, [updateLinkState, setInitialLinkStates, startPingInterval, clearPingInterval, refreshTopology]);

  /**
   * Force a clean reconnection: tear down any existing connection, cancel
   * pending reconnect timers, reset backoff, and connect immediately.
   */
  const forceReconnect = useCallback(() => {
    if (unmountedRef.current) return;
    console.log('Forcing WebSocket reconnection');

    clearPingInterval();

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    // Close existing socket without triggering the onclose reconnect logic.
    // Null out the ref first so the onclose handler (which may fire
    // asynchronously) doesn't schedule a competing reconnect.
    const oldWs = wsRef.current;
    wsRef.current = null;
    if (oldWs) {
      // Replace handlers to prevent the old socket's onclose from
      // scheduling another reconnect after we've already started one.
      oldWs.onclose = null;
      oldWs.onerror = null;
      oldWs.onmessage = null;
      oldWs.close();
    }

    setConnected(false);
    reconnectAttemptRef.current = 0;
    connect();
  }, [connect, clearPingInterval]);

  useEffect(() => {
    unmountedRef.current = false;
    connect();

    /**
     * Handle browser tab visibility changes.
     *
     * When a tab is backgrounded for extended periods, browsers freeze JS
     * execution and silently kill WebSocket connections. The onclose event
     * often doesn't fire until well after the tab is reactivated (or not at
     * all). This listener detects reactivation and forces a fresh connection
     * if the existing one is dead or stale.
     */
    const handleVisibilityChange = () => {
      if (document.hidden || unmountedRef.current) return;

      const ws = wsRef.current;
      const sinceLast = Date.now() - lastReceivedRef.current;

      if (!ws || ws.readyState !== WebSocket.OPEN || sinceLast > STALE_THRESHOLD_MS) {
        console.log(
          `Tab visible — reconnecting (readyState=${ws?.readyState}, ` +
          `silent for ${Math.round(sinceLast / 1000)}s)`
        );
        forceReconnect();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      unmountedRef.current = true;
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      clearPingInterval();
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [connect, forceReconnect, clearPingInterval]);

  const sendMessage = (message: string) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(message);
    }
  };

  return { connected, lastMessage, sendMessage };
};
