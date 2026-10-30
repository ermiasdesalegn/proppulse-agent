// What the buyer-facing API returns, and how the website turns it into addresses and wording.

export interface PortalUnit {
  id: string;
  title: string;
  project_name: string;
  unit_type: string;
  sub_city: string;
  lat: number | null;
  lng: number | null;
  price_etb: number | string;
  bedrooms: number;
  sqm: number | string;
  delivery_date: string | null;
  construction_stage_pct: number | string | null;
  down_payment_pct: number | string | null;
  est_rental_yield_pct: number | string | null;
  status: string;
  developer_name: string;
  developer_slug: string;
  has_layout: boolean;
  cover: string | null;
}

export interface Featured {
  hero?: PortalUnit | null; // the listing chosen to lead the landing page (HERO_PROPERTY_ID), if it is still for sale
  units: PortalUnit[];
  stats: { units: number; developers: number; areas: number };
  sub_cities: { sub_city: string; units: number }[];
}

/** Money and sizes arrive as decimals, which JSON may carry as text. */
export const num = (value: number | string | null | undefined): number => Number(value ?? 0);

/** Our own cover photos are served through this site; pictures hosted by a developer are used where they are. Anything else is ignored. */
export function coverSrc(cover: string | null | undefined): string | null {
  if (!cover) return null;
  if (cover.startsWith("/api/public/")) return cover;
  return /^https?:\/\//i.test(cover) ? cover : null;
}

export function specsLine(unit: Pick<PortalUnit, "bedrooms" | "sqm" | "sub_city">): string {
  const beds = unit.bedrooms === 0 ? "Studio" : `${unit.bedrooms} bed`;
  const size = num(unit.sqm) > 0 ? `${Math.round(num(unit.sqm))} m²` : null;
  return [beds, size, unit.sub_city].filter(Boolean).join(" · ");
}

export function deliveryLabel(date: string | null | undefined): string | null {
  if (!date) return null;
  const when = new Date(date);
  if (Number.isNaN(when.getTime())) return null;
  const label = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", month: "short", year: "numeric" }).format(when);
  return when.getTime() > Date.now() ? `Delivery ${label}` : "Ready to move in";
}

/** Read on the server: the showcase for the landing page. Null when the backend is unreachable, so the page still renders. */
export async function loadFeatured(): Promise<Featured | null> {
  try {
    const base = (process.env.BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
    // HERO_PROPERTY_ID picks the home whose photo leads the page (a listing id from the portal's address bar); unset, the newest leads.
    const hero = (process.env.HERO_PROPERTY_ID ?? "").trim();
    const query = /^[0-9a-f-]{36}$/i.test(hero) ? `?hero=${hero}` : "";
    const res = await fetch(`${base}/api/public/featured${query}`, { next: { revalidate: 60 }, signal: AbortSignal.timeout(4000) });
    if (!res.ok) return null;
    const featured = (await res.json()) as Featured;
    // A backend that does not know `hero` yet still lists the chosen home when it is among the featured ones.
    return { ...featured, hero: featured.hero ?? (query ? featured.units.find((u) => u.id === hero) ?? null : null) };
  } catch {
    return null;
  }
}

// ---------------- browsing: filters live in the address, so a search can be shared, bookmarked and used with the back button ----------------

export const PAGE_SIZE = 24;
export const SORTS = [
  { value: "newest", label: "Newest first" },
  { value: "price_asc", label: "Price: low to high" },
  { value: "price_desc", label: "Price: high to low" },
  { value: "size_desc", label: "Largest first" },
] as const;
export type Sort = (typeof SORTS)[number]["value"];

export interface Filters {
  q: string;
  sub_city: string;
  developer: string;
  min_bedrooms: number | null;
  price_min: number | null;
  price_max: number | null;
  sort: Sort;
  page: number;
}

type Raw = Record<string, string | string[] | undefined>;

const first = (v: string | string[] | undefined): string => (Array.isArray(v) ? v[0] : v) ?? "";
const whole = (v: string, max: number): number | null => (/^\d{1,12}$/.test(v) && Number(v) <= max ? Number(v) : null);

/** Whatever is in the address becomes a safe set of filters: unknown or malformed values are dropped, not passed on. */
export function parseFilters(raw: Raw): Filters {
  const sort = first(raw.sort);
  return {
    q: first(raw.q).trim().slice(0, 80),
    sub_city: first(raw.sub_city).trim().slice(0, 80),
    developer: /^[a-z0-9-]{1,60}$/.test(first(raw.developer)) ? first(raw.developer) : "",
    min_bedrooms: whole(first(raw.min_bedrooms), 20),
    price_min: whole(first(raw.price_min), 1e12),
    price_max: whole(first(raw.price_max), 1e12),
    sort: SORTS.some((s) => s.value === sort) ? (sort as Sort) : "newest",
    page: Math.max(1, whole(first(raw.page), 10_000) ?? 1),
  };
}

/** The filters as address parameters, leaving out anything that is at its default. */
export function filtersToParams(f: Filters, override: Partial<Filters> = {}): URLSearchParams {
  const x = { ...f, ...override };
  const p = new URLSearchParams();
  if (x.q) p.set("q", x.q);
  if (x.sub_city) p.set("sub_city", x.sub_city);
  if (x.developer) p.set("developer", x.developer);
  if (x.min_bedrooms !== null) p.set("min_bedrooms", String(x.min_bedrooms));
  if (x.price_min !== null) p.set("price_min", String(x.price_min));
  if (x.price_max !== null) p.set("price_max", String(x.price_max));
  if (x.sort !== "newest") p.set("sort", x.sort);
  if (x.page > 1) p.set("page", String(x.page));
  return p;
}

/** What the backend is asked for. The page number turns into an offset. */
export function apiQuery(f: Filters, limit = PAGE_SIZE): string {
  const p = filtersToParams(f, { page: 1 });
  p.set("limit", String(limit));
  if (f.page > 1) p.set("offset", String((f.page - 1) * limit));
  return p.toString();
}

export function hasFilters(f: Filters): boolean {
  return Boolean(f.q || f.sub_city || f.developer || f.min_bedrooms !== null || f.price_min !== null || f.price_max !== null);
}

export interface Listings {
  items: PortalUnit[];
  total: number;
  saved: string[];
  facets: {
    price_min: number | string;
    price_max: number | string;
    bedrooms_max: number;
    units: number;
    sub_cities: { sub_city: string; units: number }[];
    developers: { slug: string; name: string; units: number }[];
  };
}

export interface PortalUnitDetail extends PortalUnit {
  description: string | null;
  payment_plan: { installments?: number; interval_months?: number; handover_pct?: number } | null;
  vision_amenities: { tags?: string[] } | null;
  source_url: string | null;
  floor_plan_image_url: string | null;
  saved: boolean;
}

/** The payment terms in a sentence, from what the listing states. Null when it states none. */
export function describePlan(unit: Pick<PortalUnitDetail, "down_payment_pct" | "payment_plan">): string | null {
  const parts: string[] = [];
  const down = num(unit.down_payment_pct);
  if (down > 0) parts.push(`${Number(down.toFixed(1))}% down payment`);
  const plan = unit.payment_plan;
  if (plan?.installments && plan.interval_months) {
    const every = plan.interval_months === 1 ? "every month" : `every ${plan.interval_months} months`;
    parts.push(`${plan.installments} instalments ${every}`);
  }
  if (plan?.handover_pct) parts.push(`${Number(plan.handover_pct)}% at handover`);
  return parts.length ? parts.join(", ") : null;
}

export function pricePerSqm(unit: Pick<PortalUnit, "price_etb" | "sqm">): number | null {
  const sqm = num(unit.sqm);
  return sqm > 0 ? Math.round(num(unit.price_etb) / sqm) : null;
}
