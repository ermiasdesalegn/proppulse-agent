import { NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/session";
import { serverClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

// Ends whichever sign-in is in use: the broker's password cookie, a Supabase account, or both.
export async function POST() {
  try {
    await serverClient()?.auth.signOut();
  } catch {
    // The cookies are cleared below either way.
  }
  const response = NextResponse.json({ ok: true });
  response.cookies.set(SESSION_COOKIE, "", { httpOnly: true, sameSite: "lax", path: "/", maxAge: 0 });
  return response;
}
