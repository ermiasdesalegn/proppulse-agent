export type LatLng = [number, number];

/** Decode a Google/Mapbox encoded polyline into [lat, lng] pairs. */
export function decodePolyline(encoded: string, precision = 5): LatLng[] {
  const factor = 10 ** precision;
  const points: LatLng[] = [];
  let index = 0, lat = 0, lng = 0;
  while (index < encoded.length) {
    for (const axis of [0, 1]) {
      let shift = 0, result = 0, byte: number;
      do {
        if (index >= encoded.length) return points; // truncated input: keep what decoded cleanly
        byte = encoded.charCodeAt(index++) - 63;
        result |= (byte & 0x1f) << shift;
        shift += 5;
      } while (byte >= 0x20);
      const delta = result & 1 ? ~(result >> 1) : result >> 1;
      if (axis === 0) lat += delta; else lng += delta;
    }
    points.push([lat / factor, lng / factor]);
  }
  return points;
}

export function encodePolyline(points: LatLng[], precision = 5): string {
  const factor = 10 ** precision;
  let out = "", prevLat = 0, prevLng = 0;
  for (const [lat, lng] of points) {
    const iLat = Math.round(lat * factor), iLng = Math.round(lng * factor);
    for (const delta of [iLat - prevLat, iLng - prevLng]) {
      let v = delta < 0 ? ~(delta << 1) : delta << 1;
      while (v >= 0x20) { out += String.fromCharCode((0x20 | (v & 0x1f)) + 63); v >>= 5; }
      out += String.fromCharCode(v + 63);
    }
    prevLat = iLat; prevLng = iLng;
  }
  return out;
}
