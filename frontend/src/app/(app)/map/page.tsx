"use client";

import { useMemo, useState } from "react";
import clsx from "@/components/clsx";
import { Card, DEVELOPER_COLOR, DEVELOPER_VAR, Empty, ErrorNote, Pill } from "@/components/ui";
import { makeProjection, spreadPins } from "@/lib/geo";
import { fmtDateTime, fmtEtb, kmFromMeters } from "@/lib/format";
import { decodePolyline, type LatLng } from "@/lib/polyline";
import type { Property, Tour } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const W = 900, H = 560;
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

  const located = useMemo(() => (properties.data ?? []).filter((p) => p.lat !== null && p.lng !== null), [properties.data]);
  const tour = tours.data?.find((t) => t.id === tourId) ?? null;
  const route = useMemo(() => routePoints(tour), [tour]);
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
  const routeXY = route.map(([lat, lng]) => projection.project(lat, lng));

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Map & Tours</h1>
        <p className="text-sm text-muted">Inventory across Addis Ababa, coloured by developer. Pick a tour to see its route. This map is drawn locally and needs no map key; live Mapbox tiles can replace it later.</p>
      </div>
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
            <span className="ml-auto text-xs text-muted">{located.length} units</span>
          </div>

          {properties.loading ? <Empty>Loading the map...</Empty> : (
            <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Map of available units in Addis Ababa" onClick={() => setSelectedId(null)}>
              <rect width={W} height={H} fill="rgb(var(--raised))" />
              {[0.2, 0.4, 0.6, 0.8].map((f) => <g key={f} stroke="rgb(var(--line))" strokeWidth="0.6" strokeDasharray="3 5"><line x1={W * f} y1="0" x2={W * f} y2={H} /><line x1="0" y1={H * f} x2={W} y2={H * f} /></g>)}
              {areas.map((a) => <text key={a.name} x={a.x} y={a.y} textAnchor="middle" fontSize="20" fontWeight="700" letterSpacing="2" fill="rgb(var(--muted))" opacity="0.35">{a.name.toUpperCase()}</text>)}

              {routeXY.length > 1 && (
                <polyline points={routeXY.map((p) => `${p.x},${p.y}`).join(" ")} fill="none" stroke="rgb(var(--accent))" strokeWidth="4" strokeLinejoin="round" strokeLinecap="round" strokeDasharray="10 7" opacity="0.9" />
              )}

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
                      <p className="text-xs text-muted">{fmtDateTime(t.scheduled_start)} - {t.stops.length} stop{t.stops.length === 1 ? "" : "s"}</p>
                      {t.mapbox_route_json && <p className="tnum text-xs text-muted">{kmFromMeters(t.mapbox_route_json.distance_meters)}, about {Math.round(t.mapbox_route_json.duration_seconds / 60)} min driving</p>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>

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
