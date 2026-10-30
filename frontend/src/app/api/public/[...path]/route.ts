import { NextResponse, type NextRequest } from "next/server";
import { backendUrl, buyerHeaders, responseHeaders } from "@/lib/proxy";

// Read-only window onto the backend's public showcase (cover photos for the browser). No sign-in, no admin key.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest, { params }: { params: { path: string[] } }) {
  const url = backendUrl(["public", ...params.path], request.nextUrl.search, process.env.BACKEND_URL ?? "http://127.0.0.1:8000");
  if (!url) return NextResponse.json({ detail: "Not found" }, { status: 404 });
  try {
    const upstream = await fetch(url, { headers: buyerHeaders(request.headers, null), signal: request.signal, cache: "no-store" });
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders(upstream.headers) });
  } catch {
    return NextResponse.json({ detail: "We could not reach the service just now." }, { status: 502 });
  }
}
