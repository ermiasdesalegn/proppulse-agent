import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { decide, emailLinkRedirect, type Role } from "@/lib/access";
import { chooseOrg, cleanOrgId, ORG_COOKIE, parseOrgs, type Org } from "@/lib/org";
import { roleFromClaims, type Claims } from "@/lib/role";
import { SESSION_COOKIE, verifySession } from "@/lib/session";
import { supabaseConfig } from "@/lib/supabase/config";

// Static files and images skip the middleware; every page and API route goes through the access rules in lib/access.ts.
export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg|apple-icon.png|logo.svg|.*\\.(?:png|jpg|jpeg|svg|webp|ico)$).*)"] };

interface CookieToSet {
  name: string;
  value: string;
  options: CookieOptions;
}

// The backend is asked which organizations a signed-in person belongs to at most once a minute per sign-in, not on every click.
// A failed lookup is remembered only briefly, so a short outage does not lock members out for a minute.
const membershipCache = new Map<string, { orgs: Org[]; until: number }>();
const MEMBERSHIP_TTL_MS = 60_000;
const MEMBERSHIP_FAILURE_TTL_MS = 5_000;

async function organizationsOf(accessToken: string, key: string): Promise<Org[]> {
  const hit = membershipCache.get(key);
  if (hit && hit.until > Date.now()) return hit.orgs;
  let orgs: Org[] = [];
  let ttl = MEMBERSHIP_TTL_MS;
  try {
    const base = (process.env.BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
    const res = await fetch(`${base}/api/orgs/mine`, { headers: { authorization: `Bearer ${accessToken}` }, cache: "no-store", signal: AbortSignal.timeout(4000) });
    if (res.ok) orgs = parseOrgs(await res.json());
    else ttl = MEMBERSHIP_FAILURE_TTL_MS;
  } catch {
    ttl = MEMBERSHIP_FAILURE_TTL_MS;
  }
  if (membershipCache.size > 200) membershipCache.clear();
  membershipCache.set(key, { orgs, until: Date.now() + ttl });
  return orgs;
}

async function whoIsThis(request: NextRequest, refreshed: CookieToSet[], headers: Record<string, string>): Promise<Role> {
  // The operator's password login (the platform secret's way in; it acts for the organization named in the pp_org cookie, or the original one).
  if (await verifySession(request.cookies.get(SESSION_COOKIE)?.value, process.env.SESSION_SECRET ?? "")) return "broker";

  const cfg = supabaseConfig();
  if (!cfg || !request.cookies.getAll().some((c) => c.name.startsWith("sb-"))) return "anonymous";

  const supabase = createServerClient(cfg.url, cfg.key, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll(list, extra) {
        // A refreshed sign-in must reach both this request (so the page sees it) and the browser (so it is kept).
        for (const cookie of list) request.cookies.set(cookie.name, cookie.value);
        refreshed.push(...list);
        Object.assign(headers, extra);
      },
    },
  });
  const { data } = await supabase.auth.getClaims(); // checks the token's signature; does not trust the cookie as it is
  const claims = (data?.claims ?? null) as Claims | null;
  if (!claims?.sub || claims.is_anonymous) return roleFromClaims(claims, 0);

  const session = (await supabase.auth.getSession()).data.session; // the backend checks this token's signature itself
  const key = `${claims.sub}:${(data?.claims as { exp?: number } | undefined)?.exp}`;
  const orgs = session ? await organizationsOf(session.access_token, key) : [];

  // A member who has not chosen an agency yet (or whose remembered one is gone) gets one chosen now, so the first page they open
  // already knows which agency it is for. The backend still checks membership on every request.
  const remembered = cleanOrgId(request.cookies.get(ORG_COOKIE)?.value);
  const chosen = chooseOrg(orgs, remembered);
  if (chosen && chosen.id !== remembered) {
    request.cookies.set(ORG_COOKIE, chosen.id);
    refreshed.push({ name: ORG_COOKIE, value: chosen.id, options: { httpOnly: true, sameSite: "lax", path: "/", maxAge: 60 * 60 * 24 * 365, secure: process.env.NODE_ENV === "production" } });
  }
  return roleFromClaims(claims, orgs.length);
}

export async function middleware(request: NextRequest) {
  const { pathname, search, searchParams } = request.nextUrl;
  const emailLink = emailLinkRedirect(pathname, searchParams);
  if (emailLink) return NextResponse.redirect(new URL(emailLink, request.url));
  const refreshed: CookieToSet[] = [];
  const headers: Record<string, string> = {};
  const role = await whoIsThis(request, refreshed, headers);
  const decision = decide(pathname, role, search);

  let response: NextResponse;
  if (decision.action === "next") {
    response = NextResponse.next({ request });
  } else if (decision.action === "deny") {
    response = NextResponse.json({ detail: decision.status === 401 ? "Please sign in" : "This area is for members of an organization only" }, { status: decision.status });
  } else {
    response = NextResponse.redirect(new URL(decision.to, request.url));
  }
  for (const { name, value, options } of refreshed) response.cookies.set(name, value, options);
  for (const [name, value] of Object.entries(headers)) response.headers.set(name, value);
  return response;
}
