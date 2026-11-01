import { NextResponse, type NextRequest } from "next/server";
import { chooseOrg, cleanOrgId, ORG_COOKIE, parseOrgs, type Org } from "@/lib/org";
import { serverClient } from "@/lib/supabase/server";

// Remembers which organization the signed-in person is working in. The choice is only accepted if the backend says they belong to it,
// and even then the backend checks again on every request, so the cookie is a convenience, not a credential.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const COOKIE_OPTIONS = { httpOnly: true, sameSite: "lax", path: "/", maxAge: 60 * 60 * 24 * 365 } as const;

async function myOrgs(accessToken: string): Promise<Org[] | NextResponse> {
  const base = (process.env.BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
  try {
    const res = await fetch(`${base}/api/orgs/mine`, { headers: { authorization: `Bearer ${accessToken}` }, cache: "no-store" });
    if (!res.ok) return NextResponse.json({ detail: "We could not check your organizations just now." }, { status: 502 });
    return parseOrgs(await res.json());
  } catch {
    return NextResponse.json({ detail: "We could not reach the service just now. Please try again in a moment." }, { status: 502 });
  }
}

function remember(response: NextResponse, org: Org) {
  response.cookies.set(ORG_COOKIE, org.id, { ...COOKIE_OPTIONS, secure: process.env.NODE_ENV === "production" });
}

/** The organizations the person belongs to and the one they are working in. With none chosen yet, the first is chosen and remembered. */
export async function GET(request: NextRequest) {
  const supabase = serverClient();
  const session = supabase ? (await supabase.auth.getSession()).data.session : null;
  if (!session) return NextResponse.json({ detail: "Please sign in" }, { status: 401 });

  const orgs = await myOrgs(session.access_token);
  if (orgs instanceof NextResponse) return orgs;
  const remembered = cleanOrgId(request.cookies.get(ORG_COOKIE)?.value);
  const selected = chooseOrg(orgs, remembered);
  const response = NextResponse.json({ organizations: orgs, selected: selected?.id ?? null });
  if (selected && selected.id !== remembered) remember(response, selected);
  return response;
}

export async function POST(request: NextRequest) {
  const supabase = serverClient();
  const session = supabase ? (await supabase.auth.getSession()).data.session : null;
  if (!session) return NextResponse.json({ detail: "Please sign in" }, { status: 401 });

  const wanted = cleanOrgId(((await request.json().catch(() => ({}))) as { org_id?: string }).org_id);
  if (!wanted) return NextResponse.json({ detail: "Choose an organization" }, { status: 422 });

  const orgs = await myOrgs(session.access_token);
  if (orgs instanceof NextResponse) return orgs;
  const chosen = orgs.find((o) => o.id === wanted);
  if (!chosen) return NextResponse.json({ detail: "You do not belong to that organization" }, { status: 403 });

  const response = NextResponse.json({ selected: chosen });
  remember(response, chosen);
  return response;
}
