"use client";

import Link from "next/link";
import { useState } from "react";
import { BrandMark } from "@/components/site/SiteChrome";
import { browserClient } from "@/lib/supabase/browser";

const NOTICES: Record<string, string> = {
  confirm: "Your email is confirmed. Sign in below to continue.",
  signedout: "You have been signed out.",
  link: "That email link has expired or was already used. If your account is not confirmed yet, sign up again with the same email for a new link.",
};

const field = "mt-1 w-full rounded-md border border-line bg-bg px-3 py-2.5 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/30";
const button = "mt-5 w-full rounded-md bg-accent px-3 py-2.5 text-sm font-semibold text-white transition hover:brightness-110 disabled:opacity-50";

/** Where to go once signed in: /login hands each person to their own home (middleware), or on to where they were headed. */
function continueUrl(next: string | null): string {
  return next ? `/login?next=${encodeURIComponent(next)}` : "/login";
}

export default function LoginForm({ next, notice }: { next: string | null; notice: string | null }) {
  const [broker, setBroker] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const supabase = browserClient();

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (broker) {
        const res = await fetch("/api/auth/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ password }) });
        if (res.ok) { window.location.href = continueUrl(next); return; }
        setError((await res.json().catch(() => null))?.detail ?? "Sign-in failed.");
      } else if (supabase) {
        const { error: failure } = await supabase.auth.signInWithPassword({ email: email.trim(), password });
        if (!failure) { window.location.href = continueUrl(next); return; }
        setError(failure.message === "Email not confirmed" ? "Please confirm your email first: we sent you a link when you signed up." : "That email and password do not match.");
      }
    } catch {
      setError("We could not reach the server. Please try again.");
    }
    setBusy(false);
  }

  return (
    <main className="grid min-h-screen md:grid-cols-2">
      <section className="relative hidden flex-col justify-between overflow-hidden border-r border-line bg-gradient-to-br from-[#f3e7d3] via-[#ead7b4] to-[#d3b27a] p-12 md:flex">
        <Link href="/" className="flex items-center gap-3"><BrandMark className="h-10 w-10" /><span className="display text-xl font-medium">PropPulse AI</span></Link>
        <div>
          <h2 className="display text-5xl font-medium leading-[1.08]">Find the right home,<br /><span className="italic text-[#5c4119]">with someone who answers.</span></h2>
          <ul className="mt-6 space-y-3 text-[15px] text-[#4a3716]">
            <li>Listings from Addis Ababa developers and portals</li>
            <li>An assistant that knows every unit, in English and Amharic</li>
            <li>Site visits booked in minutes</li>
          </ul>
        </div>
        <p className="text-xs text-[#4a3716]/80">Addis Ababa real estate, with a real broker behind every chat.</p>
      </section>
      <div className="flex items-center justify-center px-4 py-10">
        <form onSubmit={submit} className="card w-full max-w-sm rounded-2xl border border-line bg-panel p-8 shadow-[0_30px_60px_-30px_rgb(60_40_10/0.35)]">
          <Link href="/" className="mb-4 inline-block md:hidden"><BrandMark /></Link>
          <p className="text-xs font-semibold uppercase tracking-widest text-accent">PropPulse AI</p>
          <h1 className="display mt-1 text-3xl font-medium">{broker ? "Broker access" : "Sign in"}</h1>
          <p className="mt-1 text-sm text-muted">{broker ? "Enter the console password." : "Welcome back. Pick up where you left off."}</p>
          {notice && NOTICES[notice] && <p role="status" className="mt-4 rounded-md border border-good/30 bg-good/10 px-3 py-2 text-sm text-good">{NOTICES[notice]}</p>}

          {!broker && (
            <>
              <label htmlFor="email" className="mt-6 block text-sm font-medium">Email</label>
              <input id="email" name="email" type="email" autoFocus autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} className={field} />
            </>
          )}
          <label htmlFor="password" className={`${broker ? "mt-6" : "mt-4"} block text-sm font-medium`}>Password</label>
          <input id="password" name="password" type="password" autoFocus={broker} autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} className={field} />

          {!broker && !supabase && <p role="alert" className="mt-3 text-sm text-warn">Buyer sign-in is not set up on this server yet.</p>}
          {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}
          <button type="submit" disabled={busy || !password || (!broker && (!email || !supabase))} className={button}>{busy ? "Signing in..." : "Sign in"}</button>

          <div className="mt-5 flex items-center justify-between text-sm text-muted">
            {broker ? <span /> : <span>New here? <Link href={next ? `/signup?next=${encodeURIComponent(next)}` : "/signup"} className="font-medium text-accent hover:underline">Create an account</Link></span>}
            <button type="button" onClick={() => { setBroker(!broker); setError(null); setPassword(""); }} className="text-xs hover:text-ink">{broker ? "Back to sign in" : "Broker access"}</button>
          </div>
        </form>
      </div>
    </main>
  );
}
