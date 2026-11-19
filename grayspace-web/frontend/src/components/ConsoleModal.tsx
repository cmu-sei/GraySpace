/**
 * Modal wrapper for console terminal
 */
import React from 'react';
import { ConsoleTerminal } from './ConsoleTerminal';

interface ConsoleModalProps {
  hostname: string | null;
  onClose: () => void;
}

export const ConsoleModal: React.FC<ConsoleModalProps> = ({ hostname, onClose }) => {
  if (!hostname) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="console-modal" onClick={(e) => e.stopPropagation()}>
        <div className="console-modal-header">
          <h3>Console: {hostname}</h3>
          <button className="close-button" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="console-modal-body">
          <ConsoleTerminal hostname={hostname} onClose={onClose} />
        </div>
      </div>
    </div>
  );
};
