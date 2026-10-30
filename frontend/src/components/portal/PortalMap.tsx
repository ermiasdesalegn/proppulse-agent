"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useMemo, useState } from "react";
import ListingCard from "@/components/site/ListingCard";
import { centroid, inAddis, makeProjection, spotKey, spreadPins } from "@/lib/geo";
import { num, type PortalUnit } from "@/lib/portal";
import type { Property } from "@/lib/types";

// A public Mapbox token (pk.) switches on live tiles. Without one the page draws its own simple map, as the console does.
const TOKEN = process.env.NEXT_PUBLIC_MAPBOX_TOKEN ?? "";
const MapboxMap = dynamic(() => import("@/components/MapboxMap"), { ssr: false, loading: () => <div className="grid h-full place-items-center text-sm text-muted">Loading the map...</div> });

const W = 900, H = 560;
const SHOWN_IN_PANEL = 5;

function shortPrice(value: number): string {
  return value >= 1_000_000 ? `${Number((value / 1_000_000).toFixed(1))}M` : `${Math.round(value / 1000)}k`;
}

type Located = PortalUnit & { lat: number; lng: number };

export default function PortalMap({ units, total }: { units: PortalUnit[]; total: number }) {
  const [selectedId, setSelectedId] = useState<string | null>(null); // a home (live map) or a spot (drawn map)
  const [problem, setProblem] = useState<string | null>(null);
  const live = TOKEN.startsWith("pk.") && !problem;

  const located = useMemo(() => units.filter((u): u is Located => u.lat !== null && u.lng !== null && inAddis(u.lat, u.lng)), [units]);

  const asProperties: Property[] = useMemo(() => located.map((u) => ({
    id: u.id, title: u.title, project_name: u.project_name, unit_type: u.unit_type, sub_city: u.sub_city, lat: u.lat, lng: u.lng,
    price_etb: num(u.price_etb), bedrooms: u.bedrooms, sqm: num(u.sqm), status: u.status, developer_name: u.developer_name, developer_slug: u.developer_slug,
  })), [located]);

  // The drawn map shows one pin per spot: many listings sit on the same point, and a stack of identical pills helps nobody.
  const drawn = useMemo(() => {
    const spots = new Map<string, Located[]>();
    for (const u of located) spots.set(spotKey(u.lat, u.lng), [...(spots.get(spotKey(u.lat, u.lng)) ?? []), u]);
    const projection = makeProjection(located.map((u) => [u.lat, u.lng]), W, H, 70);
    const entries = [...spots.entries()].map(([key, list]) => {
      const [lat, lng] = centroid(list.map((u) => [u.lat, u.lng]));
      const label = list.length === 1 ? shortPrice(num(list[0].price_etb)) : `${list.length} homes`;
      return { key, list, label, ...projection.project(lat, lng) };
    });
    const pins = spreadPins(entries.map((e) => ({ id: e.key, x: e.x, y: e.y, w: e.label.length * 7 + 20 })));
    const byArea = new Map<string, Located[]>();
    for (const u of located) byArea.set(u.sub_city, [...(byArea.get(u.sub_city) ?? []), u]);
    const areas = [...byArea.entries()].sort((a, b) => b[1].length - a[1].length).slice(0, 8).map(([name, list]) => {
      const [lat, lng] = centroid(list.map((u) => [u.lat, u.lng]));
      return { name, ...projection.project(lat, lng) };
    });
    return { pins, areas, entries: new Map(entries.map((e) => [e.key, e])) };
  }, [located]);

  const selection: Located[] = live
    ? located.filter((u) => u.id === selectedId)
    : drawn.entries.get(selectedId ?? "")?.list ?? [];
  const hidden = units.length - located.length;

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_21rem]">
      <div className="overflow-hidden rounded-3xl border border-line bg-white">
        <div className={live ? "relative h-[28rem] sm:h-[34rem]" : "relative aspect-[900/560] w-full"}>
          {located.length === 0 ? (
            <div className="grid h-full place-items-center px-6 text-center text-muted">None of these homes has a location on the map yet. Try different filters.</div>
          ) : live ? (
            <MapboxMap token={TOKEN} properties={asProperties} selectedId={selectedId} onSelect={setSelectedId} route={[]} origin={null} stops={[]} onFailure={setProblem} />
          ) : (
            <svg viewBox={`0 0 ${W} ${H}`} className="h-full w-full" role="group" aria-label="Map of homes in Addis Ababa" onClick={() => setSelectedId(null)}>
              <rect width={W} height={H} fill="rgb(var(--raised))" />
              {[0.2, 0.4, 0.6, 0.8].map((f) => <g key={f} stroke="rgb(var(--line))" strokeWidth="0.8" strokeDasharray="3 5"><line x1={W * f} y1="0" x2={W * f} y2={H} /><line x1="0" y1={H * f} x2={W} y2={H * f} /></g>)}
              {drawn.areas.map((a) => <text key={a.name} x={a.x} y={a.y} textAnchor="middle" fontSize="20" fontWeight="700" letterSpacing="2" fill="rgb(var(--muted))" opacity="0.4">{a.name.toUpperCase()}</text>)}
              {drawn.pins.map((p) => {
                const entry = drawn.entries.get(p.id)!;
                const on = selectedId === p.id;
                const many = entry.list.length > 1;
                return (
                  <g key={p.id}>
                    {p.moved && <line x1={p.x} y1={p.y} x2={p.x} y2={p.cy} stroke="rgb(var(--accent))" strokeWidth="1" opacity="0.6" />}
                    <circle cx={p.x} cy={p.y} r="3" fill="rgb(var(--accent))" />
                    <g transform={`translate(${p.x - p.w / 2},${p.cy - 11})`} className="cursor-pointer" role="button" tabIndex={0}
                      aria-label={many ? `${entry.list.length} homes in ${entry.list[0].sub_city}` : `${entry.list[0].title}, ${entry.list[0].sub_city}, ${entry.label} ETB`}
                      onClick={(e) => { e.stopPropagation(); setSelectedId(p.id); }}
                      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelectedId(p.id); } }}>
                      <rect width={p.w} height={22} rx={11} fill={on ? "rgb(var(--accent))" : many ? "rgb(var(--ink))" : "rgb(var(--panel))"} stroke="rgb(var(--accent))" strokeWidth={on ? 3 : 2} />
                      <text x={p.w / 2} y={15} textAnchor="middle" fontSize="12" fontWeight="600" className="tnum" fill={on || many ? "#fff" : "rgb(var(--ink))"}>{entry.label}</text>
                    </g>
                  </g>
                );
              })}
            </svg>
          )}
        </div>
        <p className="border-t border-line px-4 py-2.5 text-xs text-muted tnum">
          {located.length} {located.length === 1 ? "home" : "homes"} on the map
          {hidden > 0 ? ` · ${hidden} without a usable location` : ""}
          {total > units.length ? ` · showing the first ${units.length} of ${total.toLocaleString("en-US")}; narrow with the filters to see others` : ""}
          {problem && " · the live map could not load, so this is a simple drawing"}
        </p>
      </div>

      <div aria-live="polite" className="space-y-4 lg:sticky lg:top-24 lg:self-start">
        {selection.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-line bg-white/60 px-6 py-10 text-center text-sm text-muted">Choose a pin on the map to preview the homes there.</div>
        ) : (
          <>
            {selection.length > 1 && <p className="text-sm font-medium">{selection.length} homes at this spot in {selection[0].sub_city}</p>}
            {selection.slice(0, SHOWN_IN_PANEL).map((u) => <ListingCard key={u.id} unit={u} />)}
            {selection.length > SHOWN_IN_PANEL && (
              <p className="text-sm text-muted">and {selection.length - SHOWN_IN_PANEL} more. <Link href={`/portal?sub_city=${encodeURIComponent(selection[0].sub_city)}`} className="font-medium text-accent underline-offset-4 hover:underline">See them all in {selection[0].sub_city}</Link></p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
