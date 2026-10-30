// Supabase settings for the browser and the server. The public key is meant to be public (it only allows what the project's rules allow);
// the NEXT_PUBLIC_ names must be written out in full so Next can put their values into the browser bundle.

export interface SupabaseConfig {
  url: string;
  key: string;
}

export function supabaseConfig(): SupabaseConfig | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY || "";
  return url && key ? { url, key } : null;
}
