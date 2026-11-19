/**
 * Reusable console button component
 */
import React from 'react';

interface ConsoleButtonProps {
  hostname: string;
  onOpen: (hostname: string) => void;
}

export const ConsoleButton: React.FC<ConsoleButtonProps> = ({ hostname, onOpen }) => {
  return (
    <button
      className="console-button"
      onClick={(e) => {
        e.stopPropagation();
        onOpen(hostname);
      }}
      title={`Open console for ${hostname}`}
    >
      {'>_'} Console
    </button>
  );
};
