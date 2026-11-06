"use client";

import Link from "next/link";
import { useState } from "react";
import { BrandMark } from "@/components/site/SiteChrome";
import { detailOf } from "@/lib/api";

const button = "mt-5 w-full rounded-md bg-accent px-3 py-2.5 text-sm font-semibold text-white transition hover:brightness-110 disabled:opacity-50";

/** Accepts an invitation link. The backend only lets the invited email address in. */
export default function JoinForm({ token }: { token: string | null }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function join() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/invitations/accept", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ token }) });
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        setError(detailOf(body) ?? "We could not accept the invitation.");
        setBusy(false);
        return;
      }
      await fetch("/api/orgs/select", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ org_id: body.id }) });
      window.location.href = "/console";
      return;
    } catch {
      setError("We could not reach the server. Please try again.");
    }
    setBusy(false);
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="card w-full max-w-sm rounded-2xl border border-line bg-panel p-8 shadow-[0_30px_60px_-30px_rgb(60_40_10/0.35)]">
        <Link href="/" className="mb-4 inline-block"><BrandMark /></Link>
        <h1 className="display text-3xl font-medium">Join your team</h1>
        {token ? (
          <>
            <p className="mt-1 text-sm text-muted">Accept the invitation with the account you are signed in with. It only works for the email address it was sent to.</p>
            {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}
            <button type="button" onClick={join} disabled={busy} className={button}>{busy ? "Joining..." : "Accept invitation"}</button>
          </>
        ) : (
          <p role="alert" className="mt-3 text-sm text-bad">This invitation link is incomplete. Ask for a new one.</p>
        )}
      </div>
    </main>
  );
}
