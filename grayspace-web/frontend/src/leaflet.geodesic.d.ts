declare module 'leaflet.geodesic' {
  import * as L from 'leaflet';

  interface GeodesicOptions extends L.PolylineOptions {
    wrap?: boolean;
    steps?: number;
  }

  export class GeodesicLine extends L.Polyline {
    constructor(latlngs?: L.LatLngExpression[][], options?: GeodesicOptions);
  }

  export class GeodesicCircle extends L.Polygon {
    constructor(center?: L.LatLngExpression, options?: GeodesicOptions & { radius?: number });
  }
}
