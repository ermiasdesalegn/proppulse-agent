"use client";

import { useState } from "react";
import clsx from "@/components/clsx";

/** Shared look for the dashboard's charts: one hue, thin marks, 4px rounded data ends, hairline grid, values in text colours. */

export interface BarRow { label: string; value: number; note?: string }

/** Horizontal bars for comparing magnitudes. The value sits at the tip; a table view carries the same numbers. */
export function HBars({ rows, format = (v) => v.toLocaleString("en-US"), title, empty = "Nothing to show yet." }: { rows: BarRow[]; format?: (v: number) => string; title: string; empty?: string }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (rows.every((r) => r.value === 0)) return <p className="px-4 py-6 text-sm text-muted">{empty}</p>;
  return (
    <div className="px-4 py-4">
      <ul className="space-y-2" aria-label={title}>
        {rows.map((r) => (
          <li key={r.label} className="group grid grid-cols-[minmax(6rem,11rem)_1fr_auto] items-center gap-3 text-sm" title={`${r.label}: ${format(r.value)}${r.note ? ` (${r.note})` : ""}`}>
            <span className="truncate text-ink">{r.label}</span>
            <span className="relative block h-3.5 rounded-sm bg-raised">
              <span className="absolute inset-y-0 left-0 rounded-r-[4px] bg-accent transition-opacity group-hover:opacity-80" style={{ width: `${r.value === 0 ? 0 : Math.max(1.5, (r.value / max) * 100)}%` }} />
            </span>
            <span className="tnum min-w-[3.5rem] text-right text-xs text-muted">{format(r.value)}</span>
          </li>
        ))}
      </ul>
      <TableView headers={["", "Value"]} rows={rows.map((r) => [r.label, format(r.value)])} />
    </div>
  );
}

export interface ColumnPoint { label: string; value: number; detail?: string }

/** Columns over a sequence (days, score bands). The latest is the accent; the rest are a lighter step of the same hue. */
export function Columns({ points, title, format = (v) => v.toLocaleString("en-US"), emphasizeLast = false, height = 150 }: {
  points: ColumnPoint[]; title: string; format?: (v: number) => string; emphasizeLast?: boolean; height?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 560, padL = 30, padB = 22, padT = 14;
  const max = Math.max(1, ...points.map((p) => p.value));
  const nice = max <= 4 ? max : Math.ceil(max / 4) * 4;  // a round top so the ticks are whole numbers
  const ticks = nice % 2 === 0 ? [0, nice / 2, nice] : [0, nice];
  const slot = (W - padL) / Math.max(points.length, 1);
  const barW = Math.min(24, slot - 2);
  const y = (v: number) => padT + (1 - v / nice) * (height - padT - padB);
  if (points.every((p) => p.value === 0)) return <p className="px-4 py-6 text-sm text-muted">Nothing in this period yet.</p>;
  const every = Math.ceil(points.length / 8);

  return (
    <div className="px-4 py-4">
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${height}`} className="w-full" role="img" aria-label={`${title}. Highest: ${format(max)}. A table of the same values follows.`}>
          {ticks.map((v) => (
            <g key={v}>
              <line x1={padL} x2={W} y1={y(v)} y2={y(v)} stroke="rgb(var(--line))" strokeWidth="1" />
              <text x={padL - 6} y={y(v) + 3.5} textAnchor="end" fontSize="10" fill="rgb(var(--muted))" className="tnum">{v}</text>
            </g>
          ))}
          {points.map((p, i) => {
            const x = padL + i * slot + (slot - barW) / 2;
            const h = Math.max(p.value === 0 ? 0 : 2, (height - padT - padB) * (p.value / nice));
            const last = emphasizeLast && i === points.length - 1;
            return (
              <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(i)} onBlur={() => setHover(null)} tabIndex={0} aria-label={`${p.label}: ${format(p.value)}`}>
                <rect x={padL + i * slot} y={padT} width={slot} height={height - padT - padB} fill="transparent" />
                {p.value > 0 && (
                  <path d={`M ${x} ${y(0)} V ${y(0) - h + 4} Q ${x} ${y(0) - h} ${x + 4} ${y(0) - h} H ${x + barW - 4} Q ${x + barW} ${y(0) - h} ${x + barW} ${y(0) - h + 4} V ${y(0)} Z`}
                    fill={`rgb(var(--accent) / ${!emphasizeLast || last ? (hover === i ? 0.8 : 1) : hover === i ? 0.8 : 0.5})`} />
                )}
                {i % every === 0 && <text x={x + barW / 2} y={height - 6} textAnchor="middle" fontSize="10" fill="rgb(var(--muted))">{p.label}</text>}
              </g>
            );
          })}
        </svg>
        {hover !== null && (
          <div className="pointer-events-none absolute right-2 top-0 rounded-md border border-line bg-panel px-2.5 py-1.5 text-xs shadow">
            <p className="font-medium">{points[hover].label}</p>
            <p className="tnum text-muted">{format(points[hover].value)}{points[hover].detail ? ` - ${points[hover].detail}` : ""}</p>
          </div>
        )}
      </div>
      <TableView headers={["", "Value"]} rows={points.map((p) => [p.label, format(p.value)])} />
    </div>
  );
}

/** The same numbers as text, for screen readers, keyboards and anyone who prefers a table. */
export function TableView({ headers, rows }: { headers: string[]; rows: (string | number)[][] }) {
  return (
    <details className="mt-3 text-xs">
      <summary className="cursor-pointer text-muted hover:text-ink">View as table</summary>
      <table className="mt-2 w-full text-left">
        <thead className="text-muted"><tr>{headers.map((h, i) => <th key={i} className={clsx("py-1 font-medium", i > 0 && "text-right")}>{h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i} className="border-t border-line/60">{r.map((c, j) => <td key={j} className={clsx("tnum py-1", j > 0 && "text-right")}>{c}</td>)}</tr>)}</tbody>
      </table>
    </details>
  );
}
