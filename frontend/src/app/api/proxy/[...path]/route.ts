import { NextResponse, type NextRequest } from "next/server";
import { backendUrl, forwardedHeaders, responseHeaders } from "@/lib/proxy";

// The browser talks to this route; this route talks to the backend and adds the admin key.
// The key lives only in this server's environment, so it is never sent to the browser.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function forward(request: NextRequest, { params }: { params: { path: string[] } }) {
  const adminKey = process.env.ADMIN_API_KEY ?? "";
  if (!adminKey) return NextResponse.json({ detail: "ADMIN_API_KEY is not set for the dashboard server." }, { status: 503 });

  const url = backendUrl(params.path, request.nextUrl.search, process.env.BACKEND_URL ?? "http://127.0.0.1:8000");
  if (!url) return NextResponse.json({ detail: "Not found" }, { status: 404 });

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  try {
    const upstream = await fetch(url, {
      method: request.method,
      headers: forwardedHeaders(request.headers, adminKey),
      body: hasBody ? await request.arrayBuffer() : undefined,
      signal: request.signal, // when the browser goes away (an event stream closing), the backend connection closes too
      cache: "no-store",
    });
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders(upstream.headers) });
  } catch (error) {
    if ((error as Error).name === "AbortError") return new Response(null, { status: 499 });
    return NextResponse.json({ detail: "The backend is not reachable. Is it running?" }, { status: 502 });
  }
}

export { forward as GET, forward as POST, forward as PATCH };
