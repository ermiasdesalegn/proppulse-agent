import { NextResponse, type NextRequest } from "next/server";
import { safeNext } from "@/lib/access";
import { serverClient } from "@/lib/supabase/server";

// Where the link in the confirmation email lands. It trades the one-time code for a sign-in, then hands over to /login,
// which sends each person to their own home (the broker to the console, a buyer to the portal) or to where they were going.

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const { searchParams, origin } = request.nextUrl;
  const next = safeNext(searchParams.get("next"));
  const code = searchParams.get("code");
  const supabase = serverClient();

  if (supabase && code) {
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) return NextResponse.redirect(new URL(next ? `/login?next=${encodeURIComponent(next)}` : "/login", origin));
  }
  // Opened on another device than the one that signed up, the code cannot be used; the email itself is already confirmed by then.
  return NextResponse.redirect(new URL("/login?notice=confirm", origin));
}
