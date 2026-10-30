import { NextResponse, type NextRequest } from "next/server";
import { decide, type Role } from "@/lib/access";
import { SESSION_COOKIE, verifySession } from "@/lib/session";

// Static files and images skip the middleware; every page and API route goes through the access rules in lib/access.ts.
export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg|apple-icon.png|logo.svg|.*\\.(?:png|jpg|jpeg|svg|webp|ico)$).*)"] };

async function roleOf(request: NextRequest): Promise<Role> {
  const secret = process.env.SESSION_SECRET ?? "";
  // The broker's password login. Buyer accounts (Supabase) are added to this check in the next step.
  if (await verifySession(request.cookies.get(SESSION_COOKIE)?.value, secret)) return "broker";
  return "anonymous";
}

export async function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const decision = decide(pathname, await roleOf(request), search);
  if (decision.action === "next") return NextResponse.next();
  if (decision.action === "deny") {
    return NextResponse.json({ detail: decision.status === 401 ? "Please sign in" : "This area is for the broker only" }, { status: decision.status });
  }
  return NextResponse.redirect(new URL(decision.to, request.url));
}
