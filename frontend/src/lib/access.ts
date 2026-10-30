// Who may open which path. One pure function, so the middleware stays thin and every rule can be tested without a request.

export type Role = "anonymous" | "buyer" | "broker";

export type Decision =
  | { action: "next" }
  | { action: "redirect"; to: string }
  | { action: "deny"; status: 401 | 403 };

/** The first page each role lands on after signing in. */
export function homeFor(role: Role): string {
  if (role === "broker") return "/console";
  if (role === "buyer") return "/portal";
  return "/login";
}

function under(pathname: string, root: string): boolean {
  return pathname === root || pathname.startsWith(`${root}/`);
}

const PUBLIC_PAGES = ["/", "/login", "/signup", "/auth"];
const PUBLIC_API = ["/api/auth", "/api/public"];
const SIGN_IN_PAGES = ["/login", "/signup"];

function isApi(pathname: string): boolean {
  return under(pathname, "/api");
}

/** Only same-site paths survive, so a crafted ?next= cannot send someone to another site. */
export function safeNext(next: string | null | undefined): string | null {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return null;
  return next;
}

/**
 * When Supabase does not recognise the address a sign-up asked to return to, it falls back to the site's home page:
 * `/?code=...` for a good link, `/?error_code=otp_expired...` for a used or expired one. Both are passed on to where they are handled.
 */
export function emailLinkRedirect(pathname: string, params: URLSearchParams): string | null {
  if (pathname !== "/") return null;
  const code = params.get("code");
  if (code && /^[\w-]{8,200}$/.test(code)) return `/auth/callback?code=${encodeURIComponent(code)}`;
  if (params.get("error_code") || params.get("error")) return "/login?notice=link";
  return null;
}

export function decide(pathname: string, role: Role, search = ""): Decision {
  const isPublic = PUBLIC_PAGES.some((p) => (p === "/" ? pathname === "/" : under(pathname, p))) || PUBLIC_API.some((p) => under(pathname, p));
  if (isPublic) {
    // Someone who is already signed in has no use for the sign-in pages.
    if (role !== "anonymous" && SIGN_IN_PAGES.some((p) => under(pathname, p))) {
      return { action: "redirect", to: safeNext(new URLSearchParams(search).get("next")) ?? homeFor(role) };
    }
    return { action: "next" };
  }

  // Buyer area: any signed-in person. The broker may look around too.
  if (under(pathname, "/portal") || under(pathname, "/api/me")) {
    if (role !== "anonymous") return { action: "next" };
    if (isApi(pathname)) return { action: "deny", status: 401 };
    return { action: "redirect", to: `/login?next=${encodeURIComponent(pathname + search)}` };
  }

  // Everything else (the console, the admin proxy, anything new) is the broker's alone.
  if (role === "broker") return { action: "next" };
  if (role === "buyer") return isApi(pathname) ? { action: "deny", status: 403 } : { action: "redirect", to: "/portal" };
  return isApi(pathname) ? { action: "deny", status: 401 } : { action: "redirect", to: "/login" };
}
