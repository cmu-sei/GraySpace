import { applyFeatureUpdate, fallbackLayer, featuresOfKind, pointLatLng, readyToFit, toLatLngs, unwrapLine } from './mapLayer';
import { FeatureCollection, Geometry } from '../types/slices';

describe('unwrapLine', () => {
  it('passes LineStrings through', () => {
    const g: Geometry = { type: 'LineString', coordinates: [[0, 0], [10, 5]] };
    expect(unwrapLine(g)).toEqual([[0, 0], [10, 5]]);
  });

  it('joins a line split at the meridian, going east', () => {
    const g: Geometry = {
      type: 'MultiLineString',
      coordinates: [
        [[170, 10], [180, 11]],
        [[-180, 11], [-170, 10]],
      ],
    };
    expect(unwrapLine(g)).toEqual([[170, 10], [180, 11], [190, 10]]);
  });

  it('joins a line split at the meridian, going west', () => {
    const g: Geometry = {
      type: 'MultiLineString',
      coordinates: [
        [[-170, 10], [-180, 11]],
        [[180, 11], [170, 10]],
      ],
    };
    expect(unwrapLine(g)).toEqual([[-170, 10], [-180, 11], [-190, 10]]);
  });
});

describe('coordinates', () => {
  it('swaps to [lat, lng] and shifts world copies', () => {
    expect(toLatLngs([[10, 20]], 360)).toEqual([[20, 370]]);
    const point = { type: 'Feature' as const, id: 'r', geometry: { type: 'Point' as const, coordinates: [-87.6, 41.9] as [number, number] }, properties: { id: 'r', kind: 'router' as const } };
    expect(pointLatLng(point, -360)).toEqual([41.9, -447.6]);
  });
});

describe('features', () => {
  const fc: FeatureCollection = {
    type: 'FeatureCollection',
    features: [
      { type: 'Feature', id: 'a', geometry: { type: 'Point', coordinates: [0, 0] }, properties: { id: 'a', kind: 'router', status: 'running' } },
      { type: 'Feature', id: 'a:b', geometry: { type: 'LineString', coordinates: [[0, 0], [1, 1]] }, properties: { id: 'a:b', kind: 'link', state: 'up' } },
    ],
  };

  it('selects by kind', () => {
    expect(featuresOfKind(fc, 'link').map((f) => f.id)).toEqual(['a:b']);
    expect(featuresOfKind(null, 'link')).toEqual([]);
  });

  it('applies updates by id', () => {
    const next = applyFeatureUpdate(fc, 'a:b', { state: 'down', color: '#e74c3c' });
    expect(next?.features[1].properties.state).toBe('down');
    expect(next?.features[1].geometry).toBe(fc.features[1].geometry);
    expect(fc.features[1].properties.state).toBe('up');
    expect(applyFeatureUpdate(fc, 'nope', { state: 'down' })).toBe(fc);
  });
});

describe('fallbackLayer', () => {
  it('builds routers and straight links', () => {
    const routers = [
      { hostname: 'ntt-tok', isp_name: 'NTT', asn: 2914, location: { name: 'Tokyo', latitude: 35.7, longitude: 139.7 } },
      { hostname: 'ntt-sfr', isp_name: 'NTT', asn: 2914, location: { name: 'San Francisco', latitude: 37.8, longitude: -122.4 } },
    ];
    const links = [{ id: 'ntt-tok:ntt-sfr', source_hostname: 'ntt-tok', target_hostname: 'ntt-sfr', type: 'CONNECTION' }];
    const fc = fallbackLayer(routers, links, () => '#123456', () => '#5b7ea8');
    expect(featuresOfKind(fc, 'router')).toHaveLength(2);
    const link = featuresOfKind(fc, 'link')[0];
    expect(link.properties.type).toBe('INTERNAL_LINK');
    // Across the Pacific, not around the world
    expect(link.geometry).toEqual({ type: 'LineString', coordinates: [[139.7, 35.7], [237.6, 37.8]] });
  });
});

describe('readyToFit', () => {
  it('waits until the map is on screen and has a size', () => {
    // Opened on another tab: the map is mounted hidden, 0x0
    expect(readyToFit(false, { x: 0, y: 0 }, 82, false)).toBe(false);
    expect(readyToFit(true, { x: 0, y: 0 }, 82, false)).toBe(false);
    expect(readyToFit(true, { x: 1400, y: 818 }, 82, false)).toBe(true);
  });

  it('fits once, and only with routers', () => {
    expect(readyToFit(true, { x: 1400, y: 818 }, 82, true)).toBe(false);
    expect(readyToFit(true, { x: 1400, y: 818 }, 0, false)).toBe(false);
  });
});
