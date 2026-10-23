"use client";

import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import type { FeatureCollection, Point } from "geojson";
import { useEffect, useRef } from "react";
import type { LatLng } from "@/lib/polyline";
import type { Property, TourStop } from "@/lib/types";

const CENTER: [number, number] = [38.775, 9.005]; // Addis Ababa
const DEVELOPER_COLOR: Record<string, string> = { ayat: "var(--ayat)", noah: "var(--noah)", gift: "var(--gift)" };

export interface MapboxMapProps {
  token: string;
  properties: Property[];                 // the units to show (already filtered)
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  route: LatLng[];                        // [lat, lng] along the chosen tour's driving route; empty when none
  stops: TourStop[];                      // the chosen tour's stops, numbered on the map
  onFailure: (reason: string) => void;    // the page falls back to the simple drawn map
}

function shortPrice(value: number): string {
  return value >= 1_000_000 ? `${Number((value / 1_000_000).toFixed(1))}M` : `${Math.round(value / 1000)}k`;
}

const prefersDark = () => typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
const styleFor = (dark: boolean) => (dark ? "mapbox://styles/mapbox/dark-v11" : "mapbox://styles/mapbox/light-v11");

/** A feature as the map returns it from a query. */
interface Hit { properties: Record<string, unknown> | null; geometry: Point }

function toFeatures(properties: Property[]): FeatureCollection {
  return {
    type: "FeatureCollection",
    features: properties.filter((p) => p.lat !== null && p.lng !== null).map((p) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [p.lng as number, p.lat as number] },
      properties: { id: p.id, title: p.title, area: p.sub_city, price: Number(p.price_etb), dev: p.developer_slug },
    })),
  };
}

function pill(props: { id: string; title: string; price: number; dev: string }, selected: boolean, onClick: () => void): HTMLElement {
  const el = document.createElement("button");
  el.type = "button";
  el.className = "pp-pill";
  el.dataset.selected = String(selected);
  el.style.setProperty("--c", `rgb(${DEVELOPER_COLOR[props.dev] ?? "var(--accent)"})`);
  el.textContent = shortPrice(props.price);
  el.setAttribute("aria-label", `${props.title}, ${shortPrice(props.price)}`);
  el.title = props.title;
  el.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
  return el;
}

export default function MapboxMap({ token, properties, selectedId, onSelect, route, stops, onFailure }: MapboxMapProps) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const markers = useRef(new Map<string, mapboxgl.Marker>());
  const stopMarkers = useRef<mapboxgl.Marker[]>([]);
  const latest = useRef({ properties, selectedId, route, stops, onSelect });
  latest.current = { properties, selectedId, route, stops, onSelect };
  const fitted = useRef("");

  // ---- create the map once ----
  useEffect(() => {
    if (!container.current) return;
    mapboxgl.accessToken = token;
    const pills = markers.current; // the same Map for the life of the component; copied so the cleanup below does not read a changed ref
    const stops = stopMarkers;
    const map = new mapboxgl.Map({ container: container.current, style: styleFor(true), center: CENTER, zoom: 11.2, attributionControl: true });
    mapRef.current = map;
    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), "top-right");

    const failed = setTimeout(() => { if (!map.loaded()) onFailure("The map did not load in time"); }, 10_000);
    map.on("error", (e) => {
      const status = (e as unknown as { error?: { status?: number } }).error?.status;
      if (status === 401 || status === 403) onFailure("Mapbox refused the token");
    });

    const refreshMarkers = () => {
      if (!map.getSource("units") || !map.isSourceLoaded("units")) return;
      const seen = new Set<string>();
      for (const f of map.querySourceFeatures("units", { filter: ["!", ["has", "point_count"]] }) as unknown as Hit[]) {
        const p = f.properties as { id: string; title: string; price: number; dev: string };
        if (seen.has(p.id)) continue;
        seen.add(p.id);
        const selected = latest.current.selectedId === p.id;
        let marker = markers.current.get(p.id);
        if (!marker) {
          marker = new mapboxgl.Marker({ element: pill(p, selected, () => latest.current.onSelect(p.id)), anchor: "center" })
            .setLngLat(f.geometry.coordinates as [number, number]).addTo(map);
          markers.current.set(p.id, marker);
        } else {
          marker.getElement().dataset.selected = String(selected);
        }
      }
      for (const [id, marker] of markers.current) if (!seen.has(id)) { marker.remove(); markers.current.delete(id); }
    };

    const addLayers = () => {
      const dark = prefersDark();
      if (!map.getSource("units")) {
        map.addSource("units", { type: "geojson", data: toFeatures(latest.current.properties), cluster: true, clusterRadius: 46, clusterMaxZoom: 14 });
        map.addLayer({ id: "clusters", type: "circle", source: "units", filter: ["has", "point_count"],
          paint: { "circle-color": dark ? "#34beac" : "#0c766c", "circle-radius": ["step", ["get", "point_count"], 17, 5, 21, 10, 26], "circle-stroke-width": 3, "circle-stroke-color": dark ? "#141921" : "#ffffff" } });
        map.addLayer({ id: "cluster-count", type: "symbol", source: "units", filter: ["has", "point_count"],
          layout: { "text-field": ["get", "point_count_abbreviated"], "text-size": 13, "text-font": ["DIN Pro Bold", "Arial Unicode MS Bold"] }, paint: { "text-color": "#ffffff" } });
      }
      if (!map.getSource("route")) {
        map.addSource("route", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        map.addLayer({ id: "route-casing", type: "line", source: "route", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": dark ? "#141921" : "#ffffff", "line-width": 8 } });
        map.addLayer({ id: "route-line", type: "line", source: "route", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": dark ? "#34beac" : "#0c766c", "line-width": 4.5 } });
      }
      syncData();
      refreshMarkers();
    };

    const syncData = () => {
      (map.getSource("units") as mapboxgl.GeoJSONSource | undefined)?.setData(toFeatures(latest.current.properties));
      const coords = latest.current.route.map(([lat, lng]) => [lng, lat]);
      (map.getSource("route") as mapboxgl.GeoJSONSource | undefined)?.setData(
        coords.length > 1 ? { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } } : { type: "FeatureCollection", features: [] });
    };
    (map as unknown as { __sync: () => void }).__sync = syncData;

    map.on("style.load", addLayers);
    map.on("render", refreshMarkers);
    map.on("click", "clusters", (e) => {
      const feature = map.queryRenderedFeatures(e.point, { layers: ["clusters"] })[0] as unknown as Hit | undefined;
      const id = feature?.properties?.cluster_id as number | undefined;
      if (id === undefined) return;
      (map.getSource("units") as mapboxgl.GeoJSONSource).getClusterExpansionZoom(id, (err, zoom) => {
        if (!err && zoom != null) map.easeTo({ center: feature!.geometry.coordinates as [number, number], zoom: zoom + 0.5 });
      });
    });
    map.on("mouseenter", "clusters", () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "clusters", () => { map.getCanvas().style.cursor = ""; });
    map.on("click", (e) => { if (!e.defaultPrevented) latest.current.onSelect(null); });
    map.once("load", () => clearTimeout(failed));

    // follow the system theme
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onScheme = () => map.setStyle(styleFor(media.matches));
    media.addEventListener("change", onScheme);
    const resize = new ResizeObserver(() => map.resize());
    resize.observe(container.current);

    return () => {
      clearTimeout(failed);
      media.removeEventListener("change", onScheme);
      resize.disconnect();
      pills.forEach((m) => m.remove());
      pills.clear();
      stops.current.forEach((m) => m.remove());
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // ---- data changes: filter, tour ----
  useEffect(() => {
    const map = mapRef.current as (mapboxgl.Map & { __sync?: () => void }) | null;
    if (!map) return;
    const apply = () => map.__sync?.();
    if (map.isStyleLoaded()) apply(); else map.once("style.load", apply);
  }, [properties, route]);

  // ---- numbered stops ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    stopMarkers.current.forEach((m) => m.remove());
    stopMarkers.current = stops.filter((s) => s.lat !== null && s.lng !== null).map((s) => {
      const el = document.createElement("div");
      el.className = "pp-stop";
      el.textContent = String(s.stop_order);
      el.title = `${s.stop_order}. ${s.title}`;
      return new mapboxgl.Marker({ element: el, anchor: "center", offset: [0, -26] }).setLngLat([s.lng as number, s.lat as number]).addTo(map);
    });
  }, [stops]);

  // ---- selection ----
  useEffect(() => {
    const map = mapRef.current;
    for (const [id, marker] of markers.current) marker.getElement().dataset.selected = String(id === selectedId);
    const p = properties.find((x) => x.id === selectedId);
    if (map && p && p.lat !== null && p.lng !== null) map.easeTo({ center: [p.lng, p.lat], zoom: Math.max(map.getZoom(), 14.5), duration: 600 });
  }, [selectedId, properties]);

  // ---- frame the tour, or everything ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const points: [number, number][] = route.length > 1 ? route.map(([lat, lng]) => [lng, lat]) : properties.filter((p) => p.lat !== null && p.lng !== null).map((p) => [p.lng as number, p.lat as number]);
    if (!points.length) return;
    const key = `${route.length}:${points.length}:${points[0].join(",")}`;
    if (fitted.current === key) return;
    fitted.current = key;
    const bounds = points.reduce((b, c) => b.extend(c), new mapboxgl.LngLatBounds(points[0], points[0]));
    const fit = () => map.fitBounds(bounds, { padding: { top: 70, bottom: 70, left: 70, right: 70 }, maxZoom: 15, duration: 700 });
    if (map.loaded()) fit(); else map.once("load", fit);
  }, [route, properties]);

  return <div ref={container} className="h-[calc(100vh-19rem)] min-h-[28rem] w-full" role="application" aria-label="Map of available units in Addis Ababa. Price pins are buttons; the list beside the map offers the same units." />;
}
