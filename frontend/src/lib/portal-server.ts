import { serverClient } from "@/lib/supabase/server";

// Server-side reads for the Client Portal pages. The buyer's own access token goes to the backend (never the admin key), and the backend
// checks it again, so these pages show nothing that the buyer's account could not fetch itself.

export interface PortalResult<T> {
  status: number; // 401 also covers "no buyer account on this sign-in", for example the broker's password login
  data: T | null;
}

export async function portalGet<T>(path: string): Promise<PortalResult<T>> {
  const supabase = serverClient();
  const session = supabase ? (await supabase.auth.getSession()).data.session : null;
  if (!session) return { status: 401, data: null };
  try {
    const base = (process.env.BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
    const res = await fetch(`${base}/api/me${path}`, {
      headers: { authorization: `Bearer ${session.access_token}`, accept: "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    });
    return { status: res.status, data: res.ok ? ((await res.json()) as T) : null };
  } catch {
    return { status: 502, data: null };
  }
}

/** Who is signed in, for the header. Null for the broker's password login, which has no buyer account. */
export async function signedInBuyer(): Promise<{ email: string | null } | null> {
  const supabase = serverClient();
  if (!supabase) return null;
  const { data } = await supabase.auth.getClaims();
  return data?.claims?.sub ? { email: (data.claims.email as string | undefined) ?? null } : null;
}
