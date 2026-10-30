import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { supabaseConfig } from "@/lib/supabase/config";

/** For route handlers and server components. Null when Supabase is not configured. */
export function serverClient() {
  const config = supabaseConfig();
  if (!config) return null;
  const store = cookies();
  return createServerClient(config.url, config.key, {
    cookies: {
      getAll: () => store.getAll(),
      setAll(list) {
        try {
          for (const { name, value, options } of list) store.set(name, value, options);
        } catch {
          // A server component cannot set cookies; the middleware has already refreshed the session for this request.
        }
      },
    },
  });
}
