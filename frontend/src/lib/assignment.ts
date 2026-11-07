import type { Deal } from "./types";

export interface Share { user_id: string; percent: number }

/** A member shown by what is known of them: "You", or a short id (the console has no names for members yet). */
export function memberLabel(id: string | null | undefined, me: string | null | undefined): string {
  if (!id) return "Unassigned";
  return id === me ? "You" : `Member ${id.slice(0, 8)}`;
}

/** "Mine": assigned to me, or one of the people sharing the commission. Without an identity (the operator) everything shows. */
export function isMine(deal: Pick<Deal, "assigned_to" | "split">, me: string | null | undefined): boolean {
  if (!me) return true;
  return deal.assigned_to === me || (deal.split ?? []).some((s) => s.user_id === me);
}

/** The split editor's rows to a request body, or an error. Empty rows are ignored; no rows at all clears the split. */
export function splitBody(rows: { user_id: string; percent: string }[]): { shares: Share[] } | { error: string } {
  const used = rows.filter((r) => r.user_id || r.percent.trim());
  const shares: Share[] = [];
  for (const r of used) {
    const percent = Number(r.percent.replace(",", "."));
    if (!r.user_id || !Number.isFinite(percent) || percent <= 0 || percent > 100) return { error: "Choose a member and a share between 0 and 100 on every row." };
    shares.push({ user_id: r.user_id, percent });
  }
  if (new Set(shares.map((s) => s.user_id)).size !== shares.length) return { error: "Each member can appear once." };
  if (shares.length && Math.round(shares.reduce((n, s) => n + s.percent, 0) * 100) !== 10000) return { error: "The shares must add up to 100." };
  return { shares };
}
