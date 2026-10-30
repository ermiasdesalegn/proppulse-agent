import { NextResponse, type NextRequest } from "next/server";
import { backendUrl, buyerHeaders, responseHeaders } from "@/lib/proxy";
import { serverClient } from "@/lib/supabase/server";

// The signed-in buyer's way to the backend. The buyer's own access token goes along; the admin key never does.
// The backend checks the token itself, so nothing here is trusted just because it passed through.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function forward(request: NextRequest, { params }: { params: { path: string[] } }) {
  const url = backendUrl(["me", ...params.path], request.nextUrl.search, process.env.BACKEND_URL ?? "http://127.0.0.1:8000");
  if (!url) return NextResponse.json({ detail: "Not found" }, { status: 404 });

  const supabase = serverClient();
  const session = supabase ? (await supabase.auth.getSession()).data.session : null;
  if (!session) return NextResponse.json({ detail: "Please sign in" }, { status: 401 });

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  try {
    const upstream = await fetch(url, {
      method: request.method,
      headers: buyerHeaders(request.headers, session.access_token),
      body: hasBody ? await request.arrayBuffer() : undefined,
      signal: request.signal,
      cache: "no-store",
    });
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders(upstream.headers) });
  } catch (error) {
    if ((error as Error).name === "AbortError") return new Response(null, { status: 499 });
    return NextResponse.json({ detail: "We could not reach the service just now. Please try again in a moment." }, { status: 502 });
  }
}

export { forward as GET, forward as POST, forward as PUT, forward as DELETE };
