import { NextResponse, type NextRequest } from "next/server";
import { cleanOrgId, ORG_COOKIE } from "@/lib/org";
import { backendUrl, forwardedHeaders, memberHeaders, responseHeaders } from "@/lib/proxy";
import { SESSION_COOKIE, verifySession } from "@/lib/session";
import { serverClient } from "@/lib/supabase/server";

// The browser talks to this route; this route talks to the backend for the console.
// A member of an organization is forwarded with their own sign-in and the organization they chose (the backend checks both).
// The operator's password session is forwarded with the platform secret, which lives only in this server's environment.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function headersFor(request: NextRequest): Promise<Headers | NextResponse> {
  const orgId = cleanOrgId(request.cookies.get(ORG_COOKIE)?.value);
  if (await verifySession(request.cookies.get(SESSION_COOKIE)?.value, process.env.SESSION_SECRET ?? "")) {
    const adminKey = process.env.ADMIN_API_KEY ?? "";
    if (!adminKey) return NextResponse.json({ detail: "ADMIN_API_KEY is not set for the dashboard server." }, { status: 503 });
    return forwardedHeaders(request.headers, adminKey, orgId);
  }
  const supabase = serverClient();
  const session = supabase ? (await supabase.auth.getSession()).data.session : null;
  if (!session) return NextResponse.json({ detail: "Please sign in" }, { status: 401 });
  return memberHeaders(request.headers, session.access_token, orgId);
}

async function forward(request: NextRequest, { params }: { params: { path: string[] } }) {
  const url = backendUrl(params.path, request.nextUrl.search, process.env.BACKEND_URL ?? "http://127.0.0.1:8000");
  if (!url) return NextResponse.json({ detail: "Not found" }, { status: 404 });

  const headers = await headersFor(request);
  if (headers instanceof NextResponse) return headers;

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  try {
    const upstream = await fetch(url, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      signal: request.signal, // when the browser goes away (an event stream closing), the backend connection closes too
      cache: "no-store",
    });
    if (upstream.status === 401 && headers.has("x-admin-key")) {
      // The operator's session was already checked by the middleware, so a 401 here means the backend refused the platform secret. Say so:
      // a plain 401 would send the browser back to the login page, which looks like being signed out again and again.
      await upstream.body?.cancel();
      return NextResponse.json({ detail: "The backend refused the dashboard's ADMIN_API_KEY. It must be exactly the same value on Vercel and on the backend." }, { status: 502 });
    }
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders(upstream.headers) });
  } catch (error) {
    if ((error as Error).name === "AbortError") return new Response(null, { status: 499 });
    return NextResponse.json({ detail: "The backend is not reachable. Is it running?" }, { status: 502 });
  }
}

export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE };
