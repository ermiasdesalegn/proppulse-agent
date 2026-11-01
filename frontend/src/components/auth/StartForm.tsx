"use client";

import Link from "next/link";
import { useState } from "react";
import { BrandMark } from "@/components/site/SiteChrome";
import { detailOf } from "@/lib/api";
import { slugPreview } from "@/lib/org";

const field = "mt-1 w-full rounded-md border border-line bg-bg px-3 py-2.5 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/30";
const button = "mt-5 w-full rounded-md bg-accent px-3 py-2.5 text-sm font-semibold text-white transition hover:brightness-110 disabled:opacity-50";

/** Starts an agency: the person who submits becomes its owner and lands in its console. */
export default function StartForm() {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const slug = slugPreview(name);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await fetch("/api/orgs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: name.trim() }) });
      const body = await created.json().catch(() => null);
      if (!created.ok) {
        setError(detailOf(body) ?? "We could not create the agency.");
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
      <form onSubmit={submit} className="card w-full max-w-sm rounded-2xl border border-line bg-panel p-8 shadow-[0_30px_60px_-30px_rgb(60_40_10/0.35)]">
        <Link href="/" className="mb-4 inline-block"><BrandMark /></Link>
        <p className="text-xs font-semibold uppercase tracking-widest text-accent">PropPulse AI</p>
        <h1 className="display mt-1 text-3xl font-medium">Start your agency</h1>
        <p className="mt-1 text-sm text-muted">You become its owner and can invite your team afterwards.</p>
        <label htmlFor="name" className="mt-6 block text-sm font-medium">Agency name</label>
        <input id="name" name="name" autoFocus required minLength={2} maxLength={80} value={name} onChange={(e) => setName(e.target.value)} className={field} />
        {slug && <p className="mt-1 text-xs text-muted">Its address will be <span className="font-medium text-ink">{slug}</span> (made unique if taken).</p>}
        {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}
        <button type="submit" disabled={busy || name.trim().length < 2} className={button}>{busy ? "Creating..." : "Create agency"}</button>
        <p className="mt-5 text-sm text-muted"><Link href="/portal" className="hover:text-ink">Back to the homes</Link></p>
      </form>
    </main>
  );
}
