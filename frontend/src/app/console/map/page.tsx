"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { driveRoute, estimateRoute, haversineM, type DriveRoute } from "@/lib/directions";
import clsx from "@/components/clsx";
import { PageHeader, Card, DEVELOPER_COLOR, DEVELOPER_VAR, Empty, ErrorNote, Pill } from "@/components/ui";
import { makeProjection, spreadPins } from "@/lib/geo";
import { fmtDateTime, fmtEtb, kmFromMeters } from "@/lib/format";
import { decodePolyline, type LatLng } from "@/lib/polyline";
import type { Property, Tour } from "@/lib/types";
import { useApi } from "@/lib/useApi";

// A public Mapbox token (pk.) switches on live tiles, clustering and real driving routes. Without one the page draws its own map.
const TOKEN = process.env.NEXT_PUBLIC_MAPBOX_TOKEN ?? "";
const HAS_LIVE_MAP = TOKEN.startsWith("pk.");
const MapboxMap = dynamic(() => import("@/components/MapboxMap"), { ssr: false, loading: () => <Empty>Loading the map...</Empty> });

const W = 900, H = 560;
const REROUTE_METERS = 200;                       // re-plan once the broker has moved this far
const FALLBACK_ORIGIN: LatLng = [9.0107, 38.7613]; // Meskel Square, used when the browser's location is not shared
const DEVELOPERS = [{ slug: "ayat", name: "Ayat" }, { slug: "noah", name: "Noah" }, { slug: "gift", name: "Gift" }];

function shortPrice(value: number): string {
  return value >= 1_000_000 ? `${Number((value / 1_000_000).toFixed(1))}M` : `${Math.round(value / 1000)}k`;
}

function routePoints(tour: Tour | null): LatLng[] {
  if (!tour) return [];
  if (tour.mapbox_route_json) return decodePolyline(tour.mapbox_route_json.geometry);
  return tour.stops.filter((s) => s.lat !== null && s.lng !== null).map((s) => [s.lat as number, s.lng as number] as LatLng);
}

export default function MapPage() {
  const properties = useApi<Property[]>("properties");
  const tours = useApi<Tour[]>("tours", 30_000);
  const [tourId, setTourId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [developer, setDeveloper] = useState<string | null>(null);
  const [simple, setSimple] = useState(false);
  const [mapProblem, setMapProblem] = useState<string | null>(null);
  const liveMap = HAS_LIVE_MAP && !simple && !mapProblem;

  const located = useMemo(() => (properties.data ?? []).filter((p) => p.lat !== null && p.lng !== null), [properties.data]);
  const tour = tours.data?.find((t) => t.id === tourId) ?? null;

  // ---- the broker's own position, followed while a visit is chosen ----
  const [me, setMe] = useState<LatLng | null>(null);
  const [geo, setGeo] = useState<"idle" | "asking" | "ok" | "denied" | "unavailable">("idle");
  const [drive, setDrive] = useState<DriveRoute | null>(null);
  const [routing, setRouting] = useState(false);
  const wantsRoute = !!tour && tour.kind !== "CONSULTATION";

  useEffect(() => {
    if (!wantsRoute) return;
    if (!navigator.geolocation) { setGeo("unavailable"); return; }
    setGeo("asking");
    const watch = navigator.geolocation.watchPosition(
      (pos) => {
        const next: LatLng = [pos.coords.latitude, pos.coords.longitude];
        setGeo("ok");
        setMe((prev) => (prev && haversineM(prev, next) < REROUTE_METERS ? prev : next)); // only a real move re-plans the route
      },
      (err) => setGeo(err.code === err.PERMISSION_DENIED ? "denied" : "unavailable"),
      { enableHighAccuracy: true, maximumAge: 15_000, timeout: 20_000 },
    );
    return () => navigator.geolocation.clearWatch(watch);
  }, [wantsRoute]);

  // Where the visit is: the tour's stops in order, or for a project visit the project's own units.
  const destinations = useMemo<{ at: LatLng; label: string }[]>(() => {
    if (!tour || tour.kind === "CONSULTATION") return [];
    const stops = [...tour.stops].sort((a, b) => a.stop_order - b.stop_order).filter((s) => s.lat !== null && s.lng !== null);
    if (stops.length) return stops.map((s) => ({ at: [s.lat as number, s.lng as number] as LatLng, label: s.title }));
    const unit = tour.project_name ? located.find((p) => p.project_name?.toLowerCase() === tour.project_name!.toLowerCase()) : null;
    return unit ? [{ at: [unit.lat!, unit.lng!] as LatLng, label: tour.project_name! }] : [];
  }, [tour, located]);

  const origin: LatLng | null = !wantsRoute ? null : me ?? (geo === "asking" || geo === "idle" ? null : FALLBACK_ORIGIN);
  const destKey = destinations.map((d) => d.at.join(",")).join(";");

  useEffect(() => {
    setDrive(null);
    if (!origin || !destinations.length) return;
    const stops = destinations.map((d) => d.at);
    const ctl = new AbortController();
    setRouting(true);
    (HAS_LIVE_MAP ? driveRoute(TOKEN, origin, stops, ctl.signal) : Promise.reject(new Error("no token")))
      .catch((e) => { if (ctl.signal.aborted) throw e; return estimateRoute([origin, ...stops]); })
      .then((r) => setDrive(r))
      .catch(() => undefined)
      .finally(() => { if (!ctl.signal.aborted) setRouting(false); });
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [origin?.[0], origin?.[1], destKey]);

  const route = useMemo(() => drive?.points ?? routePoints(tour), [drive, tour]);
  const selected = located.find((p) => p.id === selectedId) ?? null;

  const projection = useMemo(() => makeProjection([...located.map((p) => [p.lat!, p.lng!] as LatLng), ...route], W, H, 70), [located, route]);

  // Nearby units (and units in the same building) would overlap, so spread the price pills apart and keep a dot at the true spot.
  const pins = useMemo(() => {
    const byId = new Map(located.map((p) => [p.id, p]));
    const inputs = located.map((p) => {
      const { x, y } = projection.project(p.lat!, p.lng!);
      return { id: p.id, x, y, w: shortPrice(Number(p.price_etb)).length * 7.4 + 18 };
    });
    return spreadPins(inputs).map((pin) => ({ p: byId.get(pin.id)!, ...pin }));
  }, [located, projection]);

  const areas = useMemo(() => {
    const groups = new Map<string, { xs: number[]; tops: number[] }>();
    for (const { p, cx, cy } of pins) {
      const g = groups.get(p.sub_city) ?? { xs: [], tops: [] };
      g.xs.push(cx); g.tops.push(cy);
      groups.set(p.sub_city, g);
    }
    // Each label sits just above the highest pill of its area, so no pill can cover it.
    return [...groups.entries()].map(([name, g]) => ({ name, x: g.xs.reduce((a, b) => a + b, 0) / g.xs.length, y: Math.min(...g.tops) - 22 }));
  }, [pins]);

  const stopIds = new Set(tour?.stops.map((s) => s.property_id));
  // What the live map shows: the developer filter, and only the visit's own units while a tour is chosen.
  const shown = useMemo(() => located.filter((p) => (!developer || p.developer_slug === developer) && (!tour || (stopIds.size && tour.stops.length ? stopIds.has(p.id) : !!tour.project_name && p.project_name?.toLowerCase() === tour.project_name.toLowerCase()))), [located, developer, tour]); // eslint-disable-line react-hooks/exhaustive-deps
  const routeXY = route.map(([lat, lng]) => projection.project(lat, lng));

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Inventory" title="Map & Tours" description={<>Inventory across Addis Ababa, coloured by developer. Pick a tour to see its route, the order of the stops, the distance and the driving time. {liveMap ? "Nearby units gather into a numbered cluster; zoom in or click one to open it." : HAS_LIVE_MAP ? "Showing the simple map." : "This map is drawn locally. Add a public Mapbox token to get live tiles."}</>} />
      {(properties.error || tours.error) && <ErrorNote>{properties.error ?? tours.error}</ErrorNote>}

      <div className="grid gap-4 xl:grid-cols-[1fr_20rem]">
        <Card>
          <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
            {DEVELOPERS.map((d) => (
              <button key={d.slug} onClick={() => setDeveloper(developer === d.slug ? null : d.slug)} aria-pressed={developer === d.slug}
                className={clsx("flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium", developer === d.slug ? "border-accent bg-accent/10" : "border-line hover:bg-raised")}>
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: `rgb(${DEVELOPER_VAR[d.slug]})` }} />{d.name}
              </button>
            ))}
            <span className="ml-auto text-xs text-muted">{liveMap ? shown.length : located.length} units</span>
            {HAS_LIVE_MAP && <button onClick={() => { setSimple(!liveMap); setMapProblem(null); }} className="rounded-full border border-line px-3 py-1 text-xs font-medium hover:bg-raised">{liveMap ? "Use simple map" : "Use live map"}</button>}
          </div>
          {mapProblem && <p role="status" className="border-b border-line bg-warn/10 px-4 py-2 text-xs text-warn">{mapProblem}. Showing the simple map instead.</p>}

          {properties.loading ? <Empty>Loading the map...</Empty> : liveMap ? (
            <MapboxMap token={TOKEN} properties={shown} selectedId={selectedId} onSelect={setSelectedId} route={route} origin={origin} stops={tour?.stops ?? []} onFailure={setMapProblem} />
          ) : (
            <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Map of available units in Addis Ababa" onClick={() => setSelectedId(null)}>
              <rect width={W} height={H} fill="rgb(var(--raised))" />
              {[0.2, 0.4, 0.6, 0.8].map((f) => <g key={f} stroke="rgb(var(--line))" strokeWidth="0.6" strokeDasharray="3 5"><line x1={W * f} y1="0" x2={W * f} y2={H} /><line x1="0" y1={H * f} x2={W} y2={H * f} /></g>)}
              {areas.map((a) => <text key={a.name} x={a.x} y={a.y} textAnchor="middle" fontSize="20" fontWeight="700" letterSpacing="2" fill="rgb(var(--muted))" opacity="0.35">{a.name.toUpperCase()}</text>)}

              {routeXY.length > 1 && (
                <polyline points={routeXY.map((p) => `${p.x},${p.y}`).join(" ")} fill="none" stroke="rgb(var(--accent))" strokeWidth="4" strokeLinejoin="round" strokeLinecap="round" strokeDasharray="10 7" opacity="0.9" />
              )}

              {origin && (() => {
                const o = projection.project(origin[0], origin[1]);
                return <g transform={`translate(${o.x},${o.y})`}><circle r="14" fill="rgb(var(--accent))" opacity="0.2" /><circle r="6" fill="rgb(var(--accent))" stroke="#fff" strokeWidth="2.5" /><title>Your location</title></g>;
              })()}

              {pins.map(({ p, x, y, cy, w: width, moved }) => {
                const label = shortPrice(Number(p.price_etb));
                const dim = (developer && p.developer_slug !== developer) || (tour && !stopIds.has(p.id));
                const isSel = selectedId === p.id;
                const color = `rgb(${DEVELOPER_VAR[p.developer_slug]})`;
                return (
                  <g key={p.id} opacity={dim ? 0.25 : 1}>
                    {moved && <line x1={x} y1={y} x2={x} y2={cy} stroke={color} strokeWidth="1" opacity="0.6" />}
                    <circle cx={x} cy={y} r="3" fill={color} />
                    <g transform={`translate(${x - width / 2},${cy - 11})`} className="cursor-pointer"
                    onClick={(e) => { e.stopPropagation(); setSelectedId(p.id); }} role="button" tabIndex={0}
                    aria-label={`${p.title}, ${p.sub_city}, ${fmtEtb(Number(p.price_etb))}`}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelectedId(p.id); } }}>
                    <rect width={width} height={22} rx={11} fill="rgb(var(--panel))" stroke={color} strokeWidth={isSel ? 3.5 : 2} />
                    <text x={width / 2} y={15} textAnchor="middle" fontSize="12" fontWeight="600" className="tnum" fill="rgb(var(--ink))">{label}</text>
                    <title>{`${p.title} - ${p.sub_city} - ${fmtEtb(Number(p.price_etb))}`}</title>
                    </g>
                  </g>
                );
              })}

              {tour && tour.stops.map((s) => {
                const pin = pins.find((q) => q.p.id === s.property_id); // beside the pill, which may have moved off the true spot
                if (!pin) return null;
                return (
                  <g key={s.property_id} transform={`translate(${pin.x + pin.w / 2 + 14},${pin.cy})`}>
                    <circle r="11" fill="rgb(var(--accent))" /><text y="4.5" textAnchor="middle" fontSize="13" fontWeight="700" fill="#fff">{s.stop_order}</text>
                  </g>
                );
              })}
            </svg>
          )}
        </Card>

        <div className="space-y-4">
          <Card title="Booked tours" action={tourId && <button onClick={() => setTourId(null)} className="text-xs text-muted hover:text-ink">Clear</button>}>
            {tours.loading ? <Empty>Loading...</Empty> : (tours.data ?? []).length === 0 ? (
              <Empty>No tours booked yet. Book one in the Chat Simulator or on Telegram and its route appears here.</Empty>
            ) : (
              <ul className="max-h-72 overflow-y-auto">
                {tours.data!.map((t) => (
                  <li key={t.id}>
                    <button onClick={() => setTourId(t.id === tourId ? null : t.id)} aria-pressed={t.id === tourId}
                      className={clsx("w-full border-b border-line/60 px-4 py-3 text-left text-sm hover:bg-raised", t.id === tourId && "bg-accent/10")}>
                      <p className="font-medium">{t.full_name ?? t.thread_id}</p>
                      <p className="text-xs text-muted">{fmtDateTime(t.scheduled_start)} - {t.kind === "CONSULTATION" ? "phone call" : t.project_name ? `visit to ${t.project_name}` : `${t.stops.length} stop${t.stops.length === 1 ? "" : "s"}`}</p>
                      {t.id === tourId && drive ? <p className="tnum text-xs text-muted">{kmFromMeters(drive.distanceMeters)}, about {Math.max(1, Math.round(drive.durationSeconds / 60))} min driving from {me ? "you" : "Meskel Square"}</p>
                        : t.mapbox_route_json && <p className="tnum text-xs text-muted">{kmFromMeters(t.mapbox_route_json.distance_meters)}, about {Math.round(t.mapbox_route_json.duration_seconds / 60)} min driving</p>}
                    </button>
                    <Link href={`/console/tours/${t.id}`} className="block border-b border-line/60 px-4 pb-2 text-xs font-medium text-accent hover:underline">Pre-tour dossier</Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {tour && wantsRoute && (
            <Card title="Route to the visit">
              <div className="space-y-1 px-4 py-3 text-sm" aria-live="polite">
                {routing && !drive ? <p className="text-muted">Planning the route...</p> : drive ? (() => {
                  const mins = Math.max(1, Math.round(drive.durationSeconds / 60));
                  const start = new Date(tour.scheduled_start).getTime();
                  const clock = (ms: number) => new Date(ms).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
                  return (
                    <>
                      <p className="tnum text-lg font-semibold">{mins >= 60 ? `${Math.floor(mins / 60)} h ${mins % 60} min` : `${mins} min`} <span className="text-sm font-normal text-muted">- {kmFromMeters(drive.distanceMeters)}</span></p>
                      <p className="text-xs text-muted">From {me ? "your location" : "Meskel Square"} to {destinations.map((d) => d.label).join(", then ")}</p>
                      {start > Date.now() && <p className="tnum text-xs">Leave by <strong>{clock(start - drive.durationSeconds * 1000)}</strong> to arrive at {clock(start)}. Leaving now you arrive at {clock(Date.now() + drive.durationSeconds * 1000)}.</p>}
                      {drive.legs.length > 1 && <ol className="list-decimal pl-4 text-xs text-muted">{drive.legs.map((l, i) => <li key={i} className="tnum">{destinations[i]?.label}: {kmFromMeters(l.distanceMeters)}, {Math.max(1, Math.round(l.durationSeconds / 60))} min</li>)}</ol>}
                      {drive.estimated && <p className="text-xs text-warn">Estimated from straight-line distance; live road routing is unavailable.</p>}
                    </>
                  );
                })() : <p className="text-muted">{destinations.length ? "Route unavailable." : "This visit has no mapped location yet."}</p>}
                {geo === "asking" && <p className="text-xs text-muted">Waiting for your location...</p>}
                {(geo === "denied" || geo === "unavailable") && <p className="text-xs text-warn">{geo === "denied" ? "Location access is blocked" : "Your location is unavailable"}, so the route starts from Meskel Square. Allow location in the browser to route from where you are.</p>}
                {geo === "ok" && <p className="text-xs text-muted">Following your location; the route updates as you move.</p>}
              </div>
            </Card>
          )}

          <Card title={selected ? "Selected unit" : "Units"}>
            {selected ? (
              <div className="space-y-1 px-4 py-3 text-sm">
                <p className="font-medium">{selected.title}</p>
                <p className={clsx("text-xs font-medium", DEVELOPER_COLOR[selected.developer_slug])}>{selected.developer_name}</p>
                <p className="tnum">{fmtEtb(Number(selected.price_etb))} <span className="text-muted">- {Math.round(Number(selected.price_etb) / Number(selected.sqm)).toLocaleString("en-US")} ETB/sqm</span></p>
                <p className="text-muted">{selected.sub_city} - {selected.bedrooms === 0 ? "Studio" : `${selected.bedrooms} bedrooms`} - {Number(selected.sqm)} sqm</p>
                <Pill tone={selected.status === "AVAILABLE" ? "good" : "neutral"}>{selected.status.toLowerCase()}</Pill>
              </div>
            ) : <p className="px-4 py-4 text-sm text-muted">Click a price pin for details. Pins in the same building are stacked.</p>}
          </Card>
        </div>
      </div>
    </div>
  );
}
