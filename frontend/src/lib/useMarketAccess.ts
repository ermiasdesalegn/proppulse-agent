"use client";

import { useEffect, useState } from "react";
import { canEditMarket, parseOrgs } from "@/lib/org";

let answer: Promise<boolean> | null = null;

/** Asked once per page load and shared by every component that needs it. */
function ask(): Promise<boolean> {
  answer ??= fetch("/api/orgs/select", { cache: "no-store" })
    .then(async (res) => {
      if (!res.ok) return true; // no personal sign-in: the operator's password session, which acts as the platform
      const body = await res.json();
      return canEditMarket(parseOrgs(body), typeof body?.selected === "string" ? body.selected : null, true);
    })
    .catch(() => true); // the backend is the real check; a failed lookup must not hide the buttons from the operator
  return answer;
}

/** Whether to show the controls that change the shared market data. Optimistic until known (the backend refuses anyone who may not). */
export function useMarketAccess(): boolean {
  const [allowed, setAllowed] = useState(true);
  useEffect(() => {
    let alive = true;
    void ask().then((ok) => { if (alive) setAllowed(ok); });
    return () => { alive = false; };
  }, []);
  return allowed;
}
