import { createBrowserClient } from "@supabase/ssr";
import { supabaseConfig } from "@/lib/supabase/config";

/** For client components. Null when Supabase is not configured, so the pages can say so instead of failing. */
export function browserClient() {
  const config = supabaseConfig();
  return config ? createBrowserClient(config.url, config.key) : null;
}
