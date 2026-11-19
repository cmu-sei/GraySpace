/**
 * Utility functions for formatting data in the UI
 */

/**
 * Format BGP neighbor connection type for display
 * @param type - The raw connection type from the API
 * @returns Formatted display string
 */
export const formatBGPType = (type: string): string => {
  switch (type) {
    case 'INTERNAL_LINK':
    case 'CONNECTION':
      return 'Internal';
    case 'PROVIDES_TRANSIT_TO':
      return 'Transit';
    case 'PEERS_WITH':
      return 'Peering';
    default:
      return type;
  }
};

const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];

/**
 * Format a byte count into a human-readable string (1024-based units).
 */
export const formatBytes = (bytes: number | null | undefined): string => {
  if (bytes == null) return 'N/A';
  if (bytes === 0) return '0 B';
  const i = Math.floor(Math.log(Math.abs(bytes)) / Math.log(1024));
  const idx = Math.min(i, BYTE_UNITS.length - 1);
  const value = bytes / Math.pow(1024, idx);
  return `${value.toFixed(idx === 0 ? 0 : 1)} ${BYTE_UNITS[idx]}`;
};

/**
 * Format a bytes-per-second rate into a human-readable string.
 */
export const formatBytesRate = (bytesPerSec: number | null | undefined): string => {
  if (bytesPerSec == null) return 'N/A';
  if (bytesPerSec === 0) return '0 B/s';
  const i = Math.floor(Math.log(Math.abs(bytesPerSec)) / Math.log(1024));
  const idx = Math.min(i, BYTE_UNITS.length - 1);
  const value = bytesPerSec / Math.pow(1024, idx);
  return `${value.toFixed(idx === 0 ? 0 : 1)} ${BYTE_UNITS[idx]}/s`;
};
