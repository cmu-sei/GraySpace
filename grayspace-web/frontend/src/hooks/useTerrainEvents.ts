/**
 * The Terrain API event stream (/api/v1/events): map feature updates keyed by feature id, and job
 * and instance progress.
 *
 * feature.update changes the map layer in the store without refetching geometry. Every event is
 * also re-dispatched on window as TERRAIN_EVENT for views that refresh on job and instance changes.
 * Reconnects with backoff, and only while the API token is known.
 */
import { useEffect, useRef } from 'react';
import { apiService } from '../services/api';
import { getToken } from '../services/auth';
import { useTopologyStore } from '../stores/topologyStore';
import { TerrainEvent } from '../types/slices';

export const TERRAIN_EVENT = 'grayspace:terrain-event';

export const useTerrainEvents = (enabled: boolean, onSliceChange: () => void) => {
  const updateFeature = useTopologyStore((s) => s.updateFeature);
  const onSliceChangeRef = useRef(onSliceChange);
  onSliceChangeRef.current = onSliceChange;

  useEffect(() => {
    if (!enabled || !getToken()) return;
    let ws: WebSocket | null = null;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let closed = false;

    const connect = () => {
      ws = apiService.createEventsWebSocket();
      ws.onopen = () => {
        attempt = 0;
      };
      ws.onmessage = (message) => {
        let event: TerrainEvent;
        try {
          event = JSON.parse(message.data);
        } catch {
          return;
        }
        if (event.type === 'feature.update') {
          updateFeature(event.id, event.properties);
        } else if (event.type === 'instance.update') {
          // Sites and internet hosts appear or go: fetch the layer again.
          onSliceChangeRef.current();
        }
        window.dispatchEvent(new CustomEvent(TERRAIN_EVENT, { detail: event }));
      };
      ws.onclose = () => {
        if (closed) return;
        const delay = Math.min(1000 * 2 ** attempt, 30000);
        attempt += 1;
        timer = setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, [enabled, updateFeature]);
};
