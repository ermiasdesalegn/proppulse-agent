import type { Analytics } from "./types";

/** Daily (or weekly, for long windows) message totals with the empty days filled in, so a quiet day reads as a gap and not as missing data. */
export function messageSeries(data: Pick<Analytics, "messages_per_day" | "generated_at" | "days">): { label: string; value: number; detail: string }[] {
  const end = new Date(data.generated_at);
  const weekly = data.days > 31;
  const byDay = new Map<string, { total: number; buyer: number; agent: number }>();
  for (const m of data.messages_per_day) {
    const row = byDay.get(m.day) ?? { total: 0, buyer: 0, agent: 0 };
    row.total += m.n;
    if (m.role === "buyer") row.buyer += m.n;
    if (m.role === "agent") row.agent += m.n;
    byDay.set(m.day, row);
  }
  const days: string[] = [];
  for (let i = data.days - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate() - i));
    days.push(d.toISOString().slice(0, 10));
  }
  const label = (iso: string) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  const size = weekly ? 7 : 1;
  const out = [];
  for (let i = 0; i < days.length; i += size) {
    const chunk = days.slice(i, i + size).map((d) => byDay.get(d) ?? { total: 0, buyer: 0, agent: 0 });
    const total = chunk.reduce((n, c) => n + c.total, 0);
    out.push({ label: weekly ? `${label(days[i])}` : label(days[i]), value: total, detail: `${chunk.reduce((n, c) => n + c.buyer, 0)} from buyers, ${chunk.reduce((n, c) => n + c.agent, 0)} from the agent` });
  }
  return out;
}

export const STAGE_LABEL: Record<string, string> = {
  NEW: "New", QUALIFIED: "Qualified", INVENTORY_PENDING: "Waiting for a match", SITE_VISIT_BOOKED: "Visit booked", TOUR_COMPLETED: "Tour done",
  CONTRACT_SIGNED: "Contract signed", COMMISSION_PENDING: "Commission pending", COMMISSION_PAID: "Commission paid", LOST: "Lost",
  LEAD_REGISTERED: "Registered with developer", CLOSED_LOST: "Closed, lost",
};

/** "0-19", "20-39" ... as the columns' labels. */
export const scoreBandLabel = (b: { from: number; to: number }) => `${b.from}-${b.to}`;

/** Fill the five score bands so the chart always shows the whole scale. */
export function scoreBands(rows: Analytics["score_distribution"]): { from: number; to: number; buyers: number }[] {
  return [0, 20, 40, 60, 80].map((from) => ({ from, to: from === 80 ? 100 : from + 19, buyers: rows.find((r) => r.from === from)?.buyers ?? 0 }));
}

/** Share of model requests that had to fall back to another model, as a percent (0 when there were none). */
export function fallbackRate(models: Analytics["models"]): number {
  const calls = models.reduce((n, m) => n + m.calls, 0);
  return calls ? Math.round((models.reduce((n, m) => n + m.fell_back, 0) / calls) * 100) : 0;
}
