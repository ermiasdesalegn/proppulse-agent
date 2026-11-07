"use client";

import Link from "next/link";
import { useState } from "react";
import { BrandMark } from "@/components/site/SiteChrome";
import { browserClient } from "@/lib/supabase/browser";

const field = "mt-1 w-full rounded-md border border-line bg-bg px-3 py-2.5 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/30";

export default function SignupForm({ next: requestedNext }: { next: string | null }) {
  // Everyone signs up the same way; what differs is where they land afterwards. A broker or agency goes on to start (or join) an agency.
  const [kind, setKind] = useState<"buyer" | "agency">(requestedNext === "/start" ? "agency" : "buyer");
  const next = kind === "agency" ? "/start" : requestedNext === "/start" ? null : requestedNext;
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const supabase = browserClient();

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!supabase) return;
    if (password.length < 8) { setError("Please use at least 8 characters for your password."); return; }
    setBusy(true);
    setError(null);
    const redirect = `${window.location.origin}/auth/callback${next ? `?next=${encodeURIComponent(next)}` : ""}`;
    const { data, error: failure } = await supabase.auth.signUp({
      email: email.trim(), password, options: { data: { full_name: name.trim() }, emailRedirectTo: redirect },
    });
    if (failure) {
      const text = failure.message.toLowerCase();
      // Say what we safely can. Whether an address is already registered is never revealed (Supabase answers that case like a success).
      setError(
        text.includes("password") ? failure.message
        : failure.status === 429 || text.includes("rate limit") || text.includes("too many") ? "Too many sign-up emails were sent just now. Please wait a few minutes and try again."
        : text.includes("valid email") || text.includes("invalid email") ? "That email address does not look right."
        : "We could not create the account. Check the details and try again, and if it keeps failing tell the site owner.");
      setBusy(false);
    } else if (data.session) {
      window.location.href = next ? `/login?next=${encodeURIComponent(next)}` : "/login"; // email confirmation is off: already signed in
    } else {
      setSent(true); // the same message whether or not the address was already registered, so nobody can probe for accounts
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="card w-full max-w-sm rounded-2xl border border-line bg-panel p-8 shadow-[0_30px_60px_-30px_rgb(60_40_10/0.35)]">
        <Link href="/" className="mb-4 inline-block"><BrandMark /></Link>
        <p className="text-xs font-semibold uppercase tracking-widest text-accent">PropPulse AI</p>
        {sent ? (
          <>
            <h1 className="display mt-1 text-3xl font-medium">Check your email</h1>
            <p role="status" className="mt-3 text-sm text-muted">We sent a confirmation link to <span className="font-medium text-ink">{email}</span>. Open it on this device to finish creating your account.</p>
            <p className="mt-4 text-sm text-muted">Already confirmed? <Link href="/login" className="font-medium text-accent hover:underline">Sign in</Link></p>
          </>
        ) : (
          <form onSubmit={submit}>
            <h1 className="display mt-1 text-3xl font-medium">Create your account</h1>
            <div role="radiogroup" aria-label="What brings you here" className="mt-4 grid grid-cols-2 gap-2 text-sm">
              {([["buyer", "I'm looking for a home"], ["agency", "I'm a broker or agency"]] as const).map(([value, label]) => (
                <button key={value} type="button" role="radio" aria-checked={kind === value} onClick={() => setKind(value)}
                  className={`rounded-md border px-3 py-2 text-left font-medium transition ${kind === value ? "border-accent bg-accent/10 text-ink" : "border-line text-muted hover:text-ink"}`}>
                  {label}
                </button>
              ))}
            </div>
            <p className="mt-3 text-sm text-muted">
              {kind === "agency"
                ? "Create your login first. After you confirm your email you can start your agency (you become its owner) or accept an invitation to join one."
                : "Browse every listing and talk to our assistant."}
            </p>
            <label htmlFor="name" className="mt-6 block text-sm font-medium">Full name</label>
            <input id="name" name="name" autoFocus autoComplete="name" required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} className={field} />
            <label htmlFor="email" className="mt-4 block text-sm font-medium">Email</label>
            <input id="email" name="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} className={field} />
            <label htmlFor="password" className="mt-4 block text-sm font-medium">Password</label>
            <input id="password" name="password" type="password" autoComplete="new-password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} className={field} />
            <p className="mt-1 text-xs text-muted">At least 8 characters.</p>
            {!supabase && <p role="alert" className="mt-3 text-sm text-warn">Sign-up is not set up on this server yet.</p>}
            {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}
            <button type="submit" disabled={busy || !supabase || !name.trim() || !email || !password} className="mt-5 w-full rounded-md bg-accent px-3 py-2.5 text-sm font-semibold text-white transition hover:brightness-110 disabled:opacity-50">
              {busy ? "Creating..." : "Create account"}
            </button>
            <p className="mt-5 text-sm text-muted">Already have an account? <Link href={next ? `/login?next=${encodeURIComponent(next)}` : "/login"} className="font-medium text-accent hover:underline">Sign in</Link></p>
          </form>
        )}
      </div>
    </main>
  );
}
