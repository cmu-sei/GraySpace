import React from 'react';
import { DesktopViewer } from './DesktopViewer';

interface DesktopModalProps {
  hostname: string | null;
  onClose: () => void;
}

export const DesktopModal: React.FC<DesktopModalProps> = ({ hostname, onClose }) => {
  if (!hostname) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="desktop-modal" onClick={(e) => e.stopPropagation()}>
        <div className="desktop-modal-header">
          <h3>Desktop: {hostname}</h3>
          <button className="close-button" onClick={onClose}>
            &times;
          </button>
        </div>
        <div className="desktop-modal-body">
          <DesktopViewer hostname={hostname} />
        </div>
      </div>
    </div>
  );
};
