"use client";

import Link from "next/link";
import { useEffect, useMemo } from "react";
import clsx from "@/components/clsx";
import { Empty, Pill } from "@/components/ui";
import { fmtDateTime } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { BuyerProfile } from "@/lib/types";
import { useApi } from "@/lib/useApi";

/** The score over time as a step line: it only ever goes up, each step at the moment a message earned something. */
export function ScoreHistory({ history }: { history: BuyerProfile["score"]["history"] }) {
  const W = 320, H = 96, PAD = { l: 28, r: 40, t: 10, b: 20 };
  const points = useMemo(() => {
    if (!history.length) return [];
    const times = history.map((h) => new Date(h.at).getTime());
    const t0 = times[0], span = Math.max(times[times.length - 1] - t0, 1);
    return history.map((h, i) => ({
      x: PAD.l + (history.length === 1 ? 0.5 : (times[i] - t0) / span) * (W - PAD.l - PAD.r),
      y: PAD.t + (1 - h.score / 100) * (H - PAD.t - PAD.b),
      h,
    }));
  }, [history]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!points.length) return <p className="text-xs text-muted">No score yet.</p>;

  const baseY = PAD.t + (H - PAD.t - PAD.b);
  let d = `M ${PAD.l} ${baseY}`;
  points.forEach((p, i) => { d += ` L ${p.x} ${i === 0 ? baseY : points[i - 1].y} L ${p.x} ${p.y}`; });
  d += ` L ${W - PAD.r} ${points[points.length - 1].y}`;
  const last = points[points.length - 1];

  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-md" role="img" aria-label={`Lead score over time, now ${last.h.score} of 100`}>
        {[0, 50, 100].map((v) => {
          const y = PAD.t + (1 - v / 100) * (H - PAD.t - PAD.b);
          return <g key={v}><line x1={PAD.l} x2={W - PAD.r} y1={y} y2={y} stroke="rgb(var(--line))" strokeWidth="1" /><text x={PAD.l - 6} y={y + 3.5} textAnchor="end" fontSize="10" fill="rgb(var(--muted))" className="tnum">{v}</text></g>;
        })}
        <path d={d} fill="none" stroke="rgb(var(--accent))" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {points.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r="4" fill="rgb(var(--accent))" stroke="rgb(var(--panel))" strokeWidth="2">
            <title>{`${p.h.score} - ${fmtDateTime(p.h.at)}: ${p.h.gained.map((g) => `+${g.points} ${g.label}`).join(", ") || "recorded"}`}</title>
          </circle>
        ))}
        <text x={last.x + 8} y={last.y + 4} fontSize="12" fontWeight="600" fill="rgb(var(--ink))" className="tnum">{last.h.score}</text>
      </svg>
      <figcaption className="sr-only">{history.map((h) => `${fmtDateTime(h.at)}: ${h.score}`).join("; ")}</figcaption>
    </figure>
  );
}

export default function ScorePanel({ buyerId }: { buyerId: string }) {
  const { dataVersion } = useLive();
  const profile = useApi<BuyerProfile>(`buyers/${buyerId}/profile`, 20_000);
  // Reload when the live stream reports a change; the reload function is replaced on every render, so it is left out of the list.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (dataVersion) void profile.reload() }, [dataVersion]);

  const data = profile.data;
  if (profile.loading && !data) return <Empty>Loading...</Empty>;
  if (!data) return <p className="px-4 py-3 text-sm text-muted">{profile.error ?? "Nothing to show."}</p>;
  const { score, objections, tours } = data;
  const unexplained = score.total - score.explained;

  return (
    <div className="grid gap-6 px-4 py-5 min-[1400px]:grid-cols-2">
      <section aria-labelledby="why-score">
        <h3 id="why-score" className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Why {score.total} out of 100</h3>
        <ul className="space-y-1.5">
          {score.components.map((c) => (
            <li key={c.key} className="grid grid-cols-[minmax(0,1fr)_5rem_3rem] items-center gap-3 text-sm">
              <span className={clsx(!c.earned && "text-muted")}>{c.label}</span>
              <span className="h-2 overflow-hidden rounded-full bg-raised"><span className="block h-full rounded-full bg-accent" style={{ width: `${(c.points / c.max) * 100}%` }} /></span>
              <span className="tnum text-right text-xs text-muted">{c.points}/{c.max}</span>
            </li>
          ))}
        </ul>
        {unexplained > 0 && <p className="mt-2 text-xs text-muted">{unexplained} more points were recorded before reasons were kept for this buyer.</p>}
      </section>

      <section aria-labelledby="score-history">
        <h3 id="score-history" className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">How it grew</h3>
        <ScoreHistory history={score.history} />
        {score.history.length > 0 && (
          <ul className="mt-2 space-y-0.5 text-xs text-muted">
            {score.history.slice(-3).map((h, i) => <li key={i}><time>{fmtDateTime(h.at)}</time>: {h.gained.map((g) => `+${g.points} ${g.label.toLowerCase()}`).join(", ") || `score ${h.score}`}</li>)}
          </ul>
        )}
      </section>

      <section aria-labelledby="worries">
        <h3 id="worries" className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Worries they raised</h3>
        {objections.length === 0 ? <p className="text-sm text-muted">None so far.</p> : (
          <ul className="flex flex-wrap gap-1.5">{objections.map((o) => <li key={o.text}><Pill tone="warn" title={fmtDateTime(o.at)}>{o.text}</Pill></li>)}</ul>
        )}
      </section>

      <section aria-labelledby="bookings">
        <h3 id="bookings" className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Bookings</h3>
        {tours.length === 0 ? <p className="text-sm text-muted">Nothing booked yet.</p> : (
          <ul className="space-y-1.5 text-sm">
            {tours.map((t) => (
              <li key={t.id} className="flex flex-wrap items-center justify-between gap-2">
                <span>{t.kind === "CONSULTATION" ? "Phone call" : `${t.stops.length} stop${t.stops.length === 1 ? "" : "s"}`} - {fmtDateTime(t.scheduled_start)}</span>
                <Link href={`/tours/${t.id}`} className="text-xs font-medium text-accent hover:underline">Open dossier</Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
