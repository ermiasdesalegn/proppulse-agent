import type { LatLng } from "@/lib/polyline";

export interface DriveRoute {
  points: LatLng[];        // [lat, lng] along the road
  distanceMeters: number;
  durationSeconds: number;
  legs: { distanceMeters: number; durationSeconds: number }[]; // one per destination, in order
  estimated: boolean;      // true when it is a straight-line estimate rather than a road route
}

const ROAD_FACTOR = 1.3;   // straight-line to road distance, as on the server
const AVG_SPEED_KMH = 25;  // Addis Ababa city traffic, as on the server

export function haversineM([lat1, lng1]: LatLng, [lat2, lng2]: LatLng): number {
  const rad = (d: number) => (d * Math.PI) / 180;
  const h = Math.sin(rad(lat2 - lat1) / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(rad(lng2 - lng1) / 2) ** 2;
  return 2 * 6_371_000 * Math.asin(Math.sqrt(h));
}

/** Straight lines between the points, with the same distance and time estimate the server uses. */
export function estimateRoute(path: LatLng[]): DriveRoute {
  const legs = path.slice(1).map((p, i) => {
    const meters = Math.round(haversineM(path[i], p) * ROAD_FACTOR);
    return { distanceMeters: meters, durationSeconds: Math.round((meters / 1000 / AVG_SPEED_KMH) * 3600) };
  });
  return {
    points: path, legs, estimated: true,
    distanceMeters: legs.reduce((a, l) => a + l.distanceMeters, 0),
    durationSeconds: legs.reduce((a, l) => a + l.durationSeconds, 0),
  };
}

/** Real driving route from `origin` through `stops` in order, using the Mapbox Directions API with the public token. */
export async function driveRoute(token: string, origin: LatLng, stops: LatLng[], signal?: AbortSignal): Promise<DriveRoute> {
  const coords = [origin, ...stops].map(([lat, lng]) => `${lng.toFixed(6)},${lat.toFixed(6)}`).join(";");
  const url = `https://api.mapbox.com/directions/v5/mapbox/driving-traffic/${coords}?geometries=geojson&overview=full&access_token=${encodeURIComponent(token)}`;
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`Mapbox returned ${res.status}`);
  const body = await res.json() as { code: string; routes?: { distance: number; duration: number; geometry: { coordinates: [number, number][] }; legs: { distance: number; duration: number }[] }[] };
  const route = body.routes?.[0];
  if (body.code !== "Ok" || !route) throw new Error(`Mapbox said ${body.code}`);
  return {
    points: route.geometry.coordinates.map(([lng, lat]) => [lat, lng] as LatLng),
    distanceMeters: Math.round(route.distance),
    durationSeconds: Math.round(route.duration),
    legs: route.legs.map((l) => ({ distanceMeters: Math.round(l.distance), durationSeconds: Math.round(l.duration) })),
    estimated: false,
  };
}
