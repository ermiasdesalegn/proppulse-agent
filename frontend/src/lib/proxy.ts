// Which backend URL a browser request may reach through /api/proxy/*. Everything goes to <backend>/api/<path> only.

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

export function forwardedHeaders(incoming: Headers, adminKey: string): Headers {
  const out = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = incoming.get(name);
    if (value) out.set(name, value);
  }
  out.set("x-admin-key", adminKey);
  return out;
}

const FORWARDED_RESPONSE_HEADERS = ["content-type", "cache-control", "content-length", "content-disposition"];

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
