import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { decide, type Role } from "@/lib/access";
import { mightBeBroker, roleFromClaims, type Claims } from "@/lib/role";
import { SESSION_COOKIE, verifySession } from "@/lib/session";
import { supabaseConfig } from "@/lib/supabase/config";

// Static files and images skip the middleware; every page and API route goes through the access rules in lib/access.ts.
export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg|apple-icon.png|logo.svg|.*\\.(?:png|jpg|jpeg|svg|webp|ico)$).*)"] };

interface CookieToSet {
  name: string;
  value: string;
  options: CookieOptions;
}

// The auth server is asked whether the would-be broker's email is confirmed at most once a minute per sign-in, not on every click.
const confirmedCache = new Map<string, { confirmed: boolean; until: number }>();

async function whoIsThis(request: NextRequest, refreshed: CookieToSet[], headers: Record<string, string>): Promise<Role> {
  // The broker's password login.
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
  const brokerEmail = process.env.BROKER_EMAIL ?? "";

  let confirmed = false;
  if (mightBeBroker(claims, brokerEmail)) {
    const key = `${claims?.sub}:${(data?.claims as { exp?: number } | undefined)?.exp}`;
    const hit = confirmedCache.get(key);
    if (hit && hit.until > Date.now()) {
      confirmed = hit.confirmed;
    } else {
      const { data: user } = await supabase.auth.getUser();
      confirmed = Boolean(user.user?.email_confirmed_at);
      if (confirmedCache.size > 200) confirmedCache.clear();
      confirmedCache.set(key, { confirmed, until: Date.now() + 60_000 });
    }
  }
  return roleFromClaims(claims, brokerEmail, confirmed);
}

export async function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const refreshed: CookieToSet[] = [];
  const headers: Record<string, string> = {};
  const role = await whoIsThis(request, refreshed, headers);
  const decision = decide(pathname, role, search);

  let response: NextResponse;
  if (decision.action === "next") {
    response = NextResponse.next({ request });
  } else if (decision.action === "deny") {
    response = NextResponse.json({ detail: decision.status === 401 ? "Please sign in" : "This area is for the broker only" }, { status: decision.status });
  } else {
    response = NextResponse.redirect(new URL(decision.to, request.url));
  }
  for (const { name, value, options } of refreshed) response.cookies.set(name, value, options);
  for (const [name, value] of Object.entries(headers)) response.headers.set(name, value);
  return response;
}
