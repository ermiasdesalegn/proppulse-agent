"use client";

import Link from "next/link";
import { useState } from "react";
import { Logo } from "@/components/icons";
import { browserClient } from "@/lib/supabase/browser";

const field = "mt-1 w-full rounded-md border border-line bg-bg px-3 py-2.5 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/30";

export default function SignupForm({ next }: { next: string | null }) {
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
      setError(failure.message.toLowerCase().includes("password") ? failure.message : "We could not create the account. Check the details and try again.");
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
      <div className="card w-full max-w-sm rounded-xl border border-line bg-panel p-7">
        <Link href="/" className="mb-4 inline-block"><Logo /></Link>
        <p className="text-xs font-semibold uppercase tracking-widest text-accent">PropPulse AI</p>
        {sent ? (
          <>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight">Check your email</h1>
            <p role="status" className="mt-3 text-sm text-muted">We sent a confirmation link to <span className="font-medium text-white">{email}</span>. Open it on this device to finish creating your account.</p>
            <p className="mt-4 text-sm text-muted">Already confirmed? <Link href="/login" className="font-medium text-accent hover:underline">Sign in</Link></p>
          </>
        ) : (
          <form onSubmit={submit}>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight">Create your account</h1>
            <p className="mt-1 text-sm text-muted">Browse every listing and talk to our assistant.</p>
            <label htmlFor="name" className="mt-6 block text-sm font-medium">Full name</label>
            <input id="name" name="name" autoFocus autoComplete="name" required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} className={field} />
            <label htmlFor="email" className="mt-4 block text-sm font-medium">Email</label>
            <input id="email" name="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} className={field} />
            <label htmlFor="password" className="mt-4 block text-sm font-medium">Password</label>
            <input id="password" name="password" type="password" autoComplete="new-password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} className={field} />
            <p className="mt-1 text-xs text-muted">At least 8 characters.</p>
            {!supabase && <p role="alert" className="mt-3 text-sm text-warn">Sign-up is not set up on this server yet.</p>}
            {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}
            <button type="submit" disabled={busy || !supabase || !name.trim() || !email || !password} className="mt-5 w-full rounded-md bg-accent px-3 py-2.5 text-sm font-semibold text-[#111] transition hover:brightness-110 disabled:opacity-50">
              {busy ? "Creating..." : "Create account"}
            </button>
            <p className="mt-5 text-sm text-muted">Already have an account? <Link href={next ? `/login?next=${encodeURIComponent(next)}` : "/login"} className="font-medium text-accent hover:underline">Sign in</Link></p>
          </form>
        )}
      </div>
    </main>
  );
}
