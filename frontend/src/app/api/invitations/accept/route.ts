import { NextResponse, type NextRequest } from "next/server";
import { backendUrl, buyerHeaders, responseHeaders } from "@/lib/proxy";
import { serverClient } from "@/lib/supabase/server";

// Accepting an invitation: the person's own sign-in goes along, and the backend checks that it belongs to the invited email address.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const url = backendUrl(["invitations", "accept"], "", process.env.BACKEND_URL ?? "http://127.0.0.1:8000");
  if (!url) return NextResponse.json({ detail: "Not found" }, { status: 404 });
  const supabase = serverClient();
  const session = supabase ? (await supabase.auth.getSession()).data.session : null;
  if (!session) return NextResponse.json({ detail: "Please sign in" }, { status: 401 });
  try {
    const upstream = await fetch(url, { method: "POST", headers: buyerHeaders(request.headers, session.access_token), body: await request.arrayBuffer(), cache: "no-store" });
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders(upstream.headers) });
  } catch {
    return NextResponse.json({ detail: "We could not reach the service just now. Please try again in a moment." }, { status: 502 });
  }
}
