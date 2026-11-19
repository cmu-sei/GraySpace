/**
 * Drawing the map layer (GET /api/v1/map) with Leaflet.
 *
 * The server does the drawing logic (positions, great-circle curves, meridian splits, colors), so
 * this only adapts GeoJSON to Leaflet: [lng, lat] becomes [lat, lng], and a line split at the 180°
 * meridian is joined back into one continuous line so it can be drawn on each world copy.
 */
import { Feature, FeatureCollection, FeatureKind, Geometry, Position } from '../types/slices';

// Render on 3 world copies for seamless wrapping
export const WORLD_OFFSETS = [-360, 0, 360];

export type LatLng = [number, number];

/** One continuous [lng, lat] line: the parts of a split MultiLineString shifted by ±360 to join. */
export const unwrapLine = (geometry: Geometry): Position[] => {
  if (geometry.type === 'Point') return [geometry.coordinates];
  if (geometry.type === 'LineString') return geometry.coordinates;
  const out: Position[] = [];
  let shift = 0;
  geometry.coordinates.forEach((part, i) => {
    if (i > 0 && part.length > 0) {
      const prevEnd = out[out.length - 1];
      // The previous part ended on the meridian (±180, shifted); this one starts on the other side.
      shift = prevEnd[0] - part[0][0];
    }
    part.forEach((p, j) => {
      if (i > 0 && j === 0) return; // the same meridian point as the previous part's last
      out.push([p[0] + shift, p[1]]);
    });
  });
  return out;
};

export const toLatLngs = (line: Position[], offset = 0): LatLng[] => line.map(([lng, lat]) => [lat, lng + offset]);

export const pointLatLng = (feature: Feature, offset = 0): LatLng => {
  const [lng, lat] = feature.geometry.type === 'Point' ? feature.geometry.coordinates : unwrapLine(feature.geometry)[0];
  return [lat, lng + offset];
};

export const featuresOfKind = (collection: FeatureCollection | null, kind: FeatureKind): Feature[] =>
  (collection?.features ?? []).filter((f) => f.properties.kind === kind);

/** Apply a feature.update (or /ws link_state_update) to the collection without refetching geometry. */
export const applyFeatureUpdate = (
  collection: FeatureCollection | null,
  id: string,
  properties: Record<string, unknown>,
): FeatureCollection | null => {
  if (!collection) return collection;
  let changed = false;
  const features = collection.features.map((f) => {
    if (f.id !== id) return f;
    changed = true;
    return { ...f, properties: { ...f.properties, ...properties } };
  });
  return changed ? { ...collection, features } : collection;
};

/**
 * A plain layer built from the topology routes, for when the API refuses the map layer (no token
 * yet, or none configured): straight links, no curves, colors in first-seen ISP order.
 */
export const fallbackLayer = (
  routers: { hostname: string; isp_name: string; asn: number; location: { name: string; latitude: number; longitude: number } }[],
  links: { id: string; source_hostname: string; target_hostname: string; type: string }[],
  ispColor: (isp: string) => string,
  linkColor: (state: string, type: string) => string,
): FeatureCollection => {
  const at = new Map(routers.map((r) => [r.hostname, [r.location.longitude, r.location.latitude] as Position]));
  const features: Feature[] = routers.map((r) => ({
    type: 'Feature',
    id: r.hostname,
    geometry: { type: 'Point', coordinates: [r.location.longitude, r.location.latitude] },
    properties: { id: r.hostname, kind: 'router', isp: r.isp_name, asn: r.asn, location: r.location.name, color: ispColor(r.isp_name), status: 'unknown' },
  }));
  links.forEach((l) => {
    const a = at.get(l.source_hostname);
    const b = at.get(l.target_hostname);
    if (!a || !b) return;
    // Take the short way across the Pacific: keep the line continuous past ±180.
    const end: Position = Math.abs(b[0] - a[0]) > 180 ? [b[0] + (b[0] < a[0] ? 360 : -360), b[1]] : b;
    const type = l.type === 'CONNECTION' ? 'INTERNAL_LINK' : l.type;
    features.push({
      type: 'Feature',
      id: l.id,
      geometry: { type: 'LineString', coordinates: [a, end] },
      properties: { id: l.id, kind: 'link', type: type as Feature['properties']['type'], state: 'unknown', color: linkColor('unknown', type), source: l.source_hostname, target: l.target_hostname },
    });
  });
  return { type: 'FeatureCollection', features };
};

/**
 * Whether the map can fit itself to the routers now: it is on screen, has a size, and has
 * routers, and hasn't fitted already. A hidden map (display: none) is 0x0, and fitting it zooms
 * to the street and centers off the routers.
 */
export const readyToFit = (visible: boolean, size: { x: number; y: number }, routers: number, fitted: boolean): boolean =>
  visible && !fitted && routers > 0 && size.x > 0 && size.y > 0;
