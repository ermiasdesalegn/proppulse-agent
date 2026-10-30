"use client";

import { useEffect, useRef, useState } from "react";
import { coverSrc, type PortalUnit } from "@/lib/portal";

// Warm, quiet duotones. A unit without a photo gets one of these, chosen from its id so it stays the same on every visit.
const PALETTES: [string, string, string][] = [
  ["#f1e6d3", "#d8bd90", "#8a6830"],
  ["#ebe3d5", "#bba98c", "#6f5a3a"],
  ["#e8dfce", "#c9a878", "#7a5a2c"],
  ["#eee8dc", "#a8b3a0", "#4f6247"],
  ["#f2e5d5", "#d6a583", "#8a4f2c"],
  ["#e9e3d8", "#9fb1b9", "#44606c"],
];

function hash(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return h >>> 0;
}

/** A line drawing of the kind of home, so the card still says something when there is no photograph. */
function CoverArt({ unit }: { unit: Pick<PortalUnit, "id" | "unit_type" | "sub_city" | "bedrooms"> }) {
  const h = hash(unit.id);
  const [from, to, line] = PALETTES[h % PALETTES.length];
  const gradient = `cover-${h}`;
  const house = /villa|house|bungalow/i.test(unit.unit_type);
  const floors = 4 + (h % 4);
  const cols = 3 + (h % 2);

  return (
    <svg viewBox="0 0 400 300" preserveAspectRatio="xMidYMax slice" className="h-full w-full" role="img" aria-label={`${unit.unit_type} in ${unit.sub_city}`}>
      <defs>
        <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={from} />
          <stop offset="1" stopColor={to} />
        </linearGradient>
      </defs>
      <rect width="400" height="300" fill={`url(#${gradient})`} />
      <circle cx={300 + (h % 50)} cy="70" r="26" fill="#fff" opacity=".35" />
      <path d="M0 238h400v62H0z" fill={line} opacity=".16" />
      <g fill="none" stroke={line} strokeWidth="2.2" strokeLinejoin="round" strokeLinecap="round" opacity=".85">
        {house ? (
          <>
            <path d="M110 238V150l90-62 90 62v88" />
            <path d="M96 156 200 80l104 76" />
            <rect x="178" y="188" width="44" height="50" rx="2" />
            <rect x="130" y="162" width="32" height="30" rx="2" />
            <rect x="238" y="162" width="32" height="30" rx="2" />
            <path d="M326 238v-40M326 198c-18-6-24-24-14-38 6 10 22 12 28 38-4 4-10 4-14 0Z" opacity=".7" />
          </>
        ) : (
          <>
            <rect x="120" y={238 - floors * 26} width="160" height={floors * 26} rx="3" />
            {Array.from({ length: floors }).flatMap((_, r) => Array.from({ length: cols }).map((__, c) => (
              <rect key={`${r}-${c}`} x={134 + c * (132 / cols)} y={238 - floors * 26 + 10 + r * 26} width={132 / cols - 14} height="12" rx="1.5" />
            )))}
            <path d="M300 238v-70h44v70M310 184h24M310 200h24" opacity=".6" />
            <path d="M70 238v-48h36v48" opacity=".6" />
          </>
        )}
        <path d="M30 238h340" />
      </g>
      <text x="22" y="278" fontSize="15" fill={line} opacity=".8" fontFamily="var(--font-display), Georgia, serif" letterSpacing=".5">{unit.sub_city}</text>
    </svg>
  );
}

export default function Cover({ unit, className = "" }: { unit: Pick<PortalUnit, "id" | "unit_type" | "sub_city" | "bedrooms" | "cover" | "title">; className?: string }) {
  const [failed, setFailed] = useState(false);
  const img = useRef<HTMLImageElement>(null);
  const src = coverSrc(unit.cover);
  // A photo that failed before this script ran never reports it: look at the image itself.
  useEffect(() => {
    if (img.current?.complete && img.current.naturalWidth === 0) setFailed(true);
  }, [src]);
  return (
    <div className={`relative overflow-hidden bg-raised ${className}`}>
      {src && !failed ? (
        // eslint-disable-next-line @next/next/no-img-element -- pictures come from developer websites, which Next's image optimiser is not set up to fetch
        <img ref={img} src={src} alt={`${unit.title}`} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(true)} className="h-full w-full object-cover" />
      ) : (
        <CoverArt unit={unit} />
      )}
    </div>
  );
}
