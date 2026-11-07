// An agency's own commission rate with a developer (backend migration 034): what the Commission terms page sends.

/** What to send for one row, or the message to show instead. Pure, so the rules are easy to check. */
export function termBody(rate: string, note: string, until: string): { body: { rate_pct: number; note: string | null; valid_until: string | null } } | { error: string } {
  const value = Number(rate.replace(",", "."));
  if (rate.trim() === "" || !Number.isFinite(value) || value < 0 || value > 100) return { error: "Enter a rate between 0 and 100." };
  if (until && until < new Date().toISOString().slice(0, 10)) return { error: "The end date is in the past." };
  return { body: { rate_pct: Math.round(value * 100) / 100, note: note.trim() || null, valid_until: until || null } };
}
