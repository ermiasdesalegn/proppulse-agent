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
    const res = await fetch(`${base}/api/public/featured`, { next: { revalidate: 60 }, signal: AbortSignal.timeout(4000) });
    return res.ok ? ((await res.json()) as Featured) : null;
  } catch {
    return null;
  }
}
