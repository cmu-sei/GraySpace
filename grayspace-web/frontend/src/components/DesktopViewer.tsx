import React, { useEffect, useRef, useState, useCallback } from 'react';
import { withToken } from '../services/auth';
import { websocketBase } from '../services/api';
import { loadNoVNC } from '../utils/novnc';

interface DesktopViewerProps {
  hostname: string;
}

type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

export const DesktopViewer: React.FC<DesktopViewerProps> = ({ hostname }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<RFB | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>('connecting');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [connectKey, setConnectKey] = useState(0);

  const connect = useCallback(async (isCancelled: () => boolean) => {
    let RFBClass: typeof RFB;
    try {
      RFBClass = (await loadNoVNC()).default;
    } catch (e) {
      if (isCancelled()) return;
      setStatus('error');
      setErrorMessage(e instanceof Error ? e.message : String(e));
      return;
    }
    if (isCancelled() || !containerRef.current) return;

    while (containerRef.current.firstChild) {
      containerRef.current.removeChild(containerRef.current.firstChild);
    }

    const wsUrl = withToken(`${websocketBase()}/ws/desktop/${hostname}`);

    try {
      const rfb = new RFBClass(containerRef.current, wsUrl);
      rfb.scaleViewport = true;
      rfb.resizeSession = false;
      rfb.focusOnClick = true;
      rfb.background = '#1a1a2e';

      rfb.addEventListener('connect', () => {
        setStatus('connected');
        setErrorMessage(null);
      });

      rfb.addEventListener('disconnect', (e: CustomEvent) => {
        if (e.detail?.clean) {
          setStatus('disconnected');
        } else {
          setStatus('error');
          setErrorMessage('Connection lost unexpectedly');
        }
      });

      rfb.addEventListener('credentialsrequired', () => {
        rfb.sendCredentials({ password: '' });
      });

      rfbRef.current = rfb;
      setStatus('connecting');
    } catch (e) {
      setStatus('error');
      setErrorMessage('Failed to initialize desktop connection');
    }
  }, [hostname]);

  useEffect(() => {
    let cancelled = false;
    connect(() => cancelled);

    return () => {
      cancelled = true;
      if (rfbRef.current) {
        rfbRef.current.disconnect();
        rfbRef.current = null;
      }
    };
  }, [connect, connectKey]);

  const handleReconnect = () => {
    if (rfbRef.current) {
      rfbRef.current.disconnect();
      rfbRef.current = null;
    }
    setStatus('connecting');
    setErrorMessage(null);
    setConnectKey((k) => k + 1);
  };

  return (
    <div className="desktop-viewer">
      <div className="desktop-status-bar">
        <span className={`desktop-status-indicator status-${status}`} />
        <span className="desktop-status-text">
          {status === 'connecting' && 'Connecting...'}
          {status === 'connected' && `Connected to ${hostname}`}
          {status === 'disconnected' && 'Disconnected'}
          {status === 'error' && (errorMessage || 'Connection error')}
        </span>
        {(status === 'disconnected' || status === 'error') && (
          <button className="desktop-reconnect-button" onClick={handleReconnect}>
            Reconnect
          </button>
        )}
      </div>
      <div ref={containerRef} className="desktop-canvas-container" />
    </div>
  );
};
