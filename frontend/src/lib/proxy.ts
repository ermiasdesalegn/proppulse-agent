// Which backend URL a browser request may reach through /api/proxy/*. Everything goes to <backend>/api/<path> only.
import { AGENCY_HEADER, cleanAgency } from "./agencysite";

const BLOCKED_FIRST_SEGMENTS = new Set(["telegram"]); // Telegram's own webhook is not something a browser should call

export function backendUrl(segments: string[], search: string, base: string): string | null {
  if (segments.length === 0) return null;
  for (const segment of segments) {
    if (!segment || segment === "." || segment === ".." || /[\\/%\0]/.test(segment)) return null;
  }
  if (BLOCKED_FIRST_SEGMENTS.has(segments[0])) return null;
  const root = base.replace(/\/+$/, "");
  return `${root}/api/${segments.map(encodeURIComponent).join("/")}${search}`;
}

const FORWARDED_REQUEST_HEADERS = ["content-type", "accept", "last-event-id"];

export function forwardedHeaders(incoming: Headers, adminKey: string, orgId: string | null = null): Headers {
  const out = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = incoming.get(name);
    if (value) out.set(name, value);
  }
  out.set("x-admin-key", adminKey);
  if (orgId) out.set("x-org-id", orgId);
  return out;
}

/**
 * A member of an organization reaches the console API with their own sign-in and the organization they chose. The backend checks
 * both against its memberships table, so nothing here is trusted: this only decides what is sent. The admin key is not among it.
 */
export function memberHeaders(incoming: Headers, accessToken: string, orgId: string | null): Headers {
  const out = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = incoming.get(name);
    if (value) out.set(name, value);
  }
  out.set("authorization", `Bearer ${accessToken}`);
  if (orgId) out.set("x-org-id", orgId);
  return out;
}

// No content-length: fetch has already decompressed the body (the hosted backend answers gzip), so the upstream length no longer matches and the browser would cut the answer short.
const FORWARDED_RESPONSE_HEADERS = ["content-type", "cache-control", "content-disposition"];

export function responseHeaders(upstream: Headers): Headers {
  const out = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.get(name);
    if (value) out.set(name, value);
  }
  if (upstream.get("content-type")?.includes("text/event-stream")) {
    out.set("cache-control", "no-cache, no-transform");
    out.delete("content-length");
  }
  return out;
}

// ---- the website's own routes: /api/me/* carries the buyer's sign-in, /api/public/* carries nothing. Neither ever gets the admin key. ----

const BUYER_REQUEST_HEADERS = ["content-type", "accept"];

/** The buyer's calls carry the agency the visitor chose (set by the middleware, never trusted as it arrives: only a well-formed slug is passed on). */
export function buyerHeaders(incoming: Headers, accessToken: string | null): Headers {
  const out = new Headers();
  for (const name of BUYER_REQUEST_HEADERS) {
    const value = incoming.get(name);
    if (value) out.set(name, value);
  }
  const agency = cleanAgency(incoming.get(AGENCY_HEADER));
  if (agency) out.set(AGENCY_HEADER, agency);
  if (accessToken) out.set("authorization", `Bearer ${accessToken}`);
  return out;
}
