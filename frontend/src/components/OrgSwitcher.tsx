"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Avatar } from "@/components/ui";
import { ORG_RENAMED, parseOrgs, type Org } from "@/lib/org";

/**
 * Who is signed in and which agency they are working in. A person with several agencies can switch; the choice is remembered in a
 * cookie, and the backend checks it again on every request. The operator's password session has no personal account, so it shows
 * the old generic label.
 */
export default function OrgSwitcher({ onSignOut, signOutIcon }: { onSignOut: () => void; signOutIcon: React.ReactNode }) {
  const [orgs, setOrgs] = useState<Org[] | null>(null);
  const [current, setCurrent] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => fetch("/api/orgs/select", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((body) => {
        if (!alive) return;
        setOrgs(parseOrgs(body));
        setCurrent(typeof body?.selected === "string" ? body.selected : null);
      })
      .catch(() => alive && setOrgs([]));
    void load();
    window.addEventListener(ORG_RENAMED, load); // the Agency page renames the agency: show the new name at once
    return () => { alive = false; window.removeEventListener(ORG_RENAMED, load); };
  }, []);

  async function pick(id: string) {
    const res = await fetch("/api/orgs/select", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ org_id: id }) });
    if (res.ok) window.location.reload();
  }

  const org = orgs?.find((o) => o.id === current) ?? null;
  const name = org?.name ?? "Broker";

  return (
    <div className="space-y-2">
      {orgs && orgs.length > 1 && (
        <label className="block px-2">
          <span className="sr-only">Switch agency</span>
          <select
            value={current ?? ""} onChange={(e) => pick(e.target.value)}
            className="w-full rounded-md border border-[#222] bg-[#111] px-2 py-1.5 text-xs text-white outline-none focus:border-white/30"
          >
            {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        </label>
      )}
      <div className="flex items-center gap-3 rounded-lg px-2 py-1.5">
        <Avatar name={name} className="h-8 w-8" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{name}</p>
          <p className="truncate text-xs text-muted">{org ? org.role : "Addis Ababa"}</p>
        </div>
        <button onClick={onSignOut} aria-label="Sign out" title="Sign out" className="rounded-md p-1.5 text-muted transition hover:bg-[#161616] hover:text-white">{signOutIcon}</button>
      </div>
      {orgs && orgs.length > 0 && <Link href="/start" className="block px-2 text-xs text-muted hover:text-white">Start another agency</Link>}
    </div>
  );
}
