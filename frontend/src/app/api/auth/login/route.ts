import { NextResponse, type NextRequest } from "next/server";
import { RateLimiter } from "@/lib/ratelimit";
import { SESSION_COOKIE, SESSION_TTL_SECONDS, createSession, passwordMatches } from "@/lib/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const limiter = new RateLimiter(5, 60_000); // five tries a minute per address

export async function POST(request: NextRequest) {
  const secret = process.env.SESSION_SECRET ?? "";
  const expected = process.env.DASHBOARD_PASSWORD ?? "";
  if (!secret || !expected) {
    return NextResponse.json({ detail: "The dashboard is not configured: set DASHBOARD_PASSWORD and SESSION_SECRET." }, { status: 503 });
  }
  const who = request.headers.get("x-forwarded-for")?.split(",")[0].trim() || "local";
  if (!limiter.attempt(who)) {
    return NextResponse.json({ detail: "Too many attempts. Wait a minute and try again." }, { status: 429 });
  }
  let password = "";
  try {
    password = String((await request.json()).password ?? "");
  } catch {
    return NextResponse.json({ detail: "Send the password as JSON." }, { status: 400 });
  }
  if (!(await passwordMatches(password, expected))) {
    return NextResponse.json({ detail: "That password is not right." }, { status: 401 });
  }
  limiter.reset(who);
  const response = NextResponse.json({ ok: true });
  response.cookies.set(SESSION_COOKIE, await createSession(secret, SESSION_TTL_SECONDS), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: SESSION_TTL_SECONDS,
  });
  return response;
}
