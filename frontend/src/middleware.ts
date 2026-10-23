import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, verifySession } from "@/lib/session";

// Everything except the login page, the login/logout endpoints and static assets needs a valid session.
export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico|login|api/auth).*)"] };

export async function middleware(request: NextRequest) {
  const secret = process.env.SESSION_SECRET ?? "";
  if (await verifySession(request.cookies.get(SESSION_COOKIE)?.value, secret)) return NextResponse.next();

  if (request.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json({ detail: "Please sign in" }, { status: 401 });
  }
  const login = new URL("/login", request.url);
  return NextResponse.redirect(login);
}
