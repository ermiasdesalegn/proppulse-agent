import type { LatLng } from "./polyline";

export interface Projection {
  project: (lat: number, lng: number) => { x: number; y: number };
  width: number;
  height: number;
}

/**
 * Fit points into a width x height box. Longitude is scaled by cos(latitude) so shapes are not stretched,
 * and a single point (or a degenerate box) still yields a usable, centred projection.
 */
export function makeProjection(points: LatLng[], width: number, height: number, padding = 48): Projection {
  const lats = points.map((p) => p[0]), lngs = points.map((p) => p[1]);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats), minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
  const midLat = points.length ? (minLat + maxLat) / 2 : 0;
  const k = Math.cos((midLat * Math.PI) / 180);
  if ((maxLng - minLng) * k < 1e-9 && maxLat - minLat < 1e-9) {
    // One point (or several identical ones): there is nothing to scale, so centre it.
    return { width, height, project: () => ({ x: width / 2, y: height / 2 }) };
  }
  const spanX = Math.max((maxLng - minLng) * k, 1e-6), spanY = Math.max(maxLat - minLat, 1e-6);
  const scale = Math.min((width - 2 * padding) / spanX, (height - 2 * padding) / spanY);
  const offsetX = (width - spanX * scale) / 2, offsetY = (height - spanY * scale) / 2;
  return {
    width, height,
    project: (lat, lng) => ({ x: offsetX + (lng - minLng) * k * scale, y: height - (offsetY + (lat - minLat) * scale) }),
  };
}

export interface PinInput { id: string; x: number; y: number; w: number }
export interface PlacedPin extends PinInput { cx: number; cy: number; moved: boolean }

/**
 * Place pills so none overlap. Each pill keeps its x and slides up or down in steps until it finds free space;
 * `moved` tells the caller to draw a leader line back to the true location (x, y).
 */
export function spreadPins(pins: PinInput[], height = 22, gap = 4, maxSteps = 14): PlacedPin[] {
  const placed: PlacedPin[] = [];
  const free = (cx: number, cy: number, w: number) =>
    placed.every((p) => Math.abs(cx - p.cx) >= (w + p.w) / 2 + gap || Math.abs(cy - p.cy) >= height + gap);
  for (const pin of [...pins].sort((a, b) => a.y - b.y || a.x - b.x || a.id.localeCompare(b.id))) {
    let cy = pin.y;
    for (let step = 0; step <= maxSteps * 2; step++) {
      const k = step % 2 === 0 ? step / 2 : -(step + 1) / 2; // 0, -1, +1, -2, +2 ... around the true spot
      const candidate = pin.y + k * (height + gap);
      if (free(pin.x, candidate, pin.w)) { cy = candidate; break; }
    }
    placed.push({ ...pin, cx: pin.x, cy, moved: Math.abs(cy - pin.y) > 1 });
  }
  return placed;
}

export function centroid(points: LatLng[]): LatLng {
  const n = points.length || 1;
  return [points.reduce((s, p) => s + p[0], 0) / n, points.reduce((s, p) => s + p[1], 0) / n];
}

/** Greater Addis Ababa. A scraped listing whose location falls outside it was mis-geocoded and would stretch the whole map. */
export function inAddis(lat: number, lng: number): boolean {
  return lat >= 8.75 && lat <= 9.25 && lng >= 38.6 && lng <= 39.0;
}

/** Homes at the same spot (to about 100 m) share one key, so they can be shown as one pin. */
export function spotKey(lat: number, lng: number): string {
  return `${lat.toFixed(3)},${lng.toFixed(3)}`;
}
