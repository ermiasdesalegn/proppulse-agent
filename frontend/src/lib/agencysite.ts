// Which agency a visitor to the public website is talking to. An agency lives at /<slug> (and /<slug>/portal/...); the middleware reads
// the slug from the address, checks its shape, and passes it on to every page and API call as the `x-agency` header. It is also
// remembered in a cookie, so a visitor who follows a link that does not carry the slug (the portal's own links, say) stays with the same
// agency. The backend checks the slug again on every call: an unknown or suspended agency is "not found" there.

export const AGENCY_HEADER = "x-agency";
export const AGENCY_COOKIE = "pp_agency";

// The same shape the backend accepts (app/access.py AGENCY_SLUG).
const SLUG = /^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$/;

// The website's own first path segments. An agency cannot take one of these as its address (the backend refuses them as slugs too).
const OWN_ROUTES = new Set(["api", "auth", "console", "portal", "login", "signup", "join", "start", "agencies", "_next", "public", "static"]);

/** The slug if it is a well-formed agency address, else null. Nothing else ever becomes a header. */
export function cleanAgency(value: string | null | undefined): string | null {
  const slug = (value ?? "").trim().toLowerCase();
  return SLUG.test(slug) && !OWN_ROUTES.has(slug) ? slug : null;
}

export interface Routed {
  /** The agency this request is for, or null for the original one. */
  slug: string | null;
  /** The page or API path to serve: the address without its agency prefix. */
  path: string;
  /** The agency came from the address (not from the cookie): remember it. */
  remember: boolean;
  /** The visitor opened the bare home page: forget any remembered agency. */
  forget: boolean;
}

export function routeAgency(pathname: string, cookie: string | null | undefined): Routed {
  const [, first = "", ...rest] = pathname.split("/");
  const named = cleanAgency(first);
  if (named) return { slug: named, path: `/${rest.join("/")}`, remember: true, forget: false };
  if (pathname === "/") return { slug: null, path: "/", remember: false, forget: Boolean(cookie) };
  return { slug: cleanAgency(cookie), path: pathname, remember: false, forget: false };
}

/** An address on the public website that stays with the agency: "/" becomes "/<slug>", "/#homes" becomes "/<slug>#homes". */
export function agencyHref(slug: string | null | undefined, href: string): string {
  const clean = cleanAgency(slug);
  if (!clean || !href.startsWith("/")) return href;
  return href === "/" ? `/${clean}` : href.startsWith("/#") ? `/${clean}${href.slice(1)}` : href;
}

/** Colours and logo addresses arrive from an agency's own settings: only plain shapes are used. */
export function safeColor(value: string | null | undefined): string | null {
  return /^#[0-9a-fA-F]{6}$/.test(value ?? "") ? (value as string) : null;
}

/** "#aa3300" as the "170 51 0" the theme's colour variables use, or null when it is not a plain colour. */
export function colorChannels(value: string | null | undefined): string | null {
  const hex = safeColor(value);
  return hex ? [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(" ") : null;
}

export function safeLogo(value: string | null | undefined): string | null {
  return /^https:\/\/[^\s<>"'\\]{1,280}$/.test(value ?? "") ? (value as string) : null;
}

export interface AgencyCard {
  slug: string;
  name: string;
  brand_color: string | null;
  logo_url: string | null;
}

/** The header the backend is sent for this agency: nothing at all for the original agency. */
export function agencyRequestHeaders(slug: string | null | undefined): Record<string, string> {
  const clean = cleanAgency(slug);
  return clean ? { "x-agency": clean } : {};
}
