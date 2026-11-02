// An agency's own price on top of the shared catalogue (backend migration 028): what the Projects page shows and sends.

export interface PricedProject {
  price_from_etb: number | string | null;
  price_note: string | null;
  shared_price_from_etb?: number | string | null;
  agency_price_etb?: number | string | null;
  agency_price_until?: string | null;
}

const num = (v: number | string | null | undefined): number | null => (v === null || v === undefined || v === "" ? null : Number(v));

/** Whether this agency shows a price of its own for the project. */
export function hasOwnPrice(p: PricedProject): boolean {
  return num(p.agency_price_etb) !== null;
}

/** The line under the price: where it comes from, and the shared price when this agency's differs from it. */
export function priceSource(p: PricedProject, fmt: (n: number) => string): string | null {
  if (!hasOwnPrice(p)) return null;
  const shared = num(p.shared_price_from_etb);
  const until = p.agency_price_until ? ` until ${p.agency_price_until}` : "";
  return `Your agency's price${until}. Shared price: ${shared === null ? "on request" : `from ${fmt(shared)}`}.`;
}

/** The body of PUT /api/agency-prices/project/{id}, or an error message to show instead. */
export function agencyPriceBody(price: string, note: string, until: string, today: string): { body: { price_etb: number; note: string | null; valid_until: string | null } } | { error: string } {
  const value = Number(price);
  if (!(value > 0) || value >= 1e12) return { error: "Enter a price in birr, above zero." };
  if (until && until < today) return { error: "The end date is in the past." };
  return { body: { price_etb: value, note: note.trim() || null, valid_until: until || null } };
}
