/**
 * ISP color mapping utilities
 */

// 15 distinct colors for ISPs
const ISP_COLORS = [
  '#e74c3c', // Red
  '#3498db', // Blue
  '#2ecc71', // Green
  '#f39c12', // Orange
  '#9b59b6', // Purple
  '#1abc9c', // Turquoise
  '#34495e', // Dark gray
  '#e67e22', // Carrot
  '#95a5a6', // Gray
  '#16a085', // Green Sea
  '#c0392b', // Dark red
  '#2980b9', // Belize blue
  '#27ae60', // Nephritis green
  '#8e44ad', // Wisteria purple
  '#f1c40f', // Yellow
];

const colorCache = new Map<string, string>();
let colorIndex = 0;

export const getISPColor = (ispName: string): string => {
  if (colorCache.has(ispName)) {
    return colorCache.get(ispName)!;
  }

  const color = ISP_COLORS[colorIndex % ISP_COLORS.length];
  colorCache.set(ispName, color);
  colorIndex++;

  return color;
};

export const getLinkColor = (state: string | undefined, linkType?: string): string => {
  // Down/shutdown override link type coloring
  if (state === 'down') return '#e74c3c';    // Red - draws attention to problems
  if (state === 'shutdown') return '#f39c12'; // Orange - admin action

  // Color by link type
  switch (linkType) {
    case 'INTERNAL_LINK':
    case 'CONNECTION':
      return '#5b7ea8'; // Steel blue - internal ISP infrastructure
    case 'PEERS_WITH':
      return '#2ecc71'; // Green - free peering exchange
    case 'PROVIDES_TRANSIT_TO':
      return '#e67e22'; // Orange - paid transit
    default:
      return '#5b7ea8'; // Steel blue (unknown)
  }
};

export const getStateColor = (state: string | undefined): string => {
  switch (state) {
    case 'up':
    case 'full':
      return '#2ecc71'; // Green
    case 'down':
      return '#e74c3c'; // Red
    case 'shutdown':
    case 'degraded':
      return '#f39c12'; // Orange
    default:
      return '#95a5a6'; // Gray
  }
};

export const resetColorCache = () => {
  colorCache.clear();
  colorIndex = 0;
};
