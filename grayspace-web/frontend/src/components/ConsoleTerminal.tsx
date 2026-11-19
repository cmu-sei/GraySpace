/**
 * Console terminal component using xterm.js
 */
import React, { useEffect, useRef, useState, useCallback } from 'react';
import { Terminal } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';
import { WebLinksAddon } from 'xterm-addon-web-links';
import { apiService } from '../services/api';
import 'xterm/css/xterm.css';

interface ConsoleTerminalProps {
  hostname: string;
  onClose: () => void;
}

type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

export const ConsoleTerminal: React.FC<ConsoleTerminalProps> = ({
  hostname,
  onClose,
}) => {
  const terminalRef = useRef<HTMLDivElement>(null);
  const terminalInstance = useRef<Terminal | null>(null);
  const fitAddon = useRef<FitAddon | null>(null);
  const websocket = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>('connecting');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const connect = useCallback(() => {
    if (!terminalRef.current) return;

    // Create terminal
    const term = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      theme: {
        background: '#1a1a2e',
        foreground: '#e0e0e0',
        cursor: '#00ff00',
        cursorAccent: '#1a1a2e',
        selectionBackground: 'rgba(255, 255, 255, 0.3)',
      },
      scrollback: 5000,
    });

    // Add addons
    const fit = new FitAddon();
    const webLinks = new WebLinksAddon();
    term.loadAddon(fit);
    term.loadAddon(webLinks);

    // Open terminal
    term.open(terminalRef.current);
    fit.fit();

    terminalInstance.current = term;
    fitAddon.current = fit;

    // Get terminal dimensions
    const cols = term.cols;
    const rows = term.rows;

    // Connect WebSocket
    const ws = apiService.createConsoleWebSocket(hostname, cols, rows);
    websocket.current = ws;

    ws.onopen = () => {
      term.writeln(`Connecting to ${hostname}...`);
    };

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);

        switch (message.type) {
          case 'connected':
            setStatus('connected');
            term.clear();
            break;
          case 'output':
            term.write(message.data);
            break;
          case 'error':
            setStatus('error');
            setErrorMessage(message.message);
            term.writeln(`\r\n\x1b[31mError: ${message.message}\x1b[0m`);
            break;
          case 'disconnected':
            setStatus('disconnected');
            term.writeln('\r\n\x1b[33mSession disconnected\x1b[0m');
            break;
          case 'pong':
            // Keepalive response
            break;
        }
      } catch (e) {
        console.error('Failed to parse WebSocket message:', e);
      }
    };

    ws.onerror = () => {
      setStatus('error');
      setErrorMessage('WebSocket connection failed');
      term.writeln('\r\n\x1b[31mConnection error\x1b[0m');
    };

    ws.onclose = () => {
      if (status === 'connected') {
        setStatus('disconnected');
        term.writeln('\r\n\x1b[33mConnection closed\x1b[0m');
      }
    };

    // Handle terminal input
    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data }));
      }
    });

    // Handle terminal resize
    term.onResize(({ cols, rows }) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols, rows }));
      }
    });

    // Keepalive ping every 30 seconds
    const pingInterval = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 30000);

    return () => {
      clearInterval(pingInterval);
    };
  }, [hostname, status]);

  useEffect(() => {
    const cleanup = connect();

    // Handle window resize
    const handleResize = () => {
      if (fitAddon.current) {
        fitAddon.current.fit();
      }
    };
    window.addEventListener('resize', handleResize);

    // Use ResizeObserver for container size changes (e.g., panel resizing)
    let resizeObserver: ResizeObserver | null = null;
    if (terminalRef.current) {
      resizeObserver = new ResizeObserver(() => {
        if (fitAddon.current) {
          fitAddon.current.fit();
        }
      });
      resizeObserver.observe(terminalRef.current);
    }

    return () => {
      window.removeEventListener('resize', handleResize);
      if (resizeObserver) {
        resizeObserver.disconnect();
      }
      if (cleanup) cleanup();
      if (websocket.current) {
        websocket.current.close();
      }
      if (terminalInstance.current) {
        terminalInstance.current.dispose();
      }
    };
  }, [connect]);

  const handleReconnect = () => {
    // Clean up existing connection
    if (websocket.current) {
      websocket.current.close();
    }
    if (terminalInstance.current) {
      terminalInstance.current.dispose();
    }

    // Reset state
    setStatus('connecting');
    setErrorMessage(null);

    // Reconnect
    connect();
  };

  const getStatusColor = () => {
    switch (status) {
      case 'connected':
        return '#2ecc71';
      case 'connecting':
        return '#f39c12';
      case 'disconnected':
        return '#95a5a6';
      case 'error':
        return '#e74c3c';
      default:
        return '#95a5a6';
    }
  };

  return (
    <div className="console-terminal">
      {/* Terminal Header */}
      <div className="terminal-header">
        <div className="terminal-status">
          <span
            className="status-indicator"
            style={{ backgroundColor: getStatusColor() }}
          />
          <span className="status-text">
            {status === 'connected' && `Connected to ${hostname}`}
            {status === 'connecting' && `Connecting to ${hostname}...`}
            {status === 'disconnected' && 'Disconnected'}
            {status === 'error' && (errorMessage || 'Connection error')}
          </span>
        </div>
        <div className="terminal-actions">
          {(status === 'disconnected' || status === 'error') && (
            <button className="reconnect-button" onClick={handleReconnect}>
              Reconnect
            </button>
          )}
        </div>
      </div>

      {/* Terminal Container */}
      <div ref={terminalRef} className="terminal-container" />
    </div>
  );
};
