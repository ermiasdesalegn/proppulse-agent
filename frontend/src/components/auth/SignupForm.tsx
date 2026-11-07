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
  const [show, setShow] = useState(false);
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

  const signInHref = next ? `/login?next=${encodeURIComponent(next)}` : "/login";
  const ROLES = {
    buyer: {
      title: "I'm looking for a home",
      lead: "Browse every listing, save the ones you like and talk to our assistant any time.",
      points: ["Search 580+ homes across Addis Ababa", "Ask the assistant in English or Amharic", "Book a site visit in minutes"],
      after: "You can start browsing as soon as you confirm your email.",
    },
    agency: {
      title: "I'm a broker or agency",
      lead: "Run your own workspace: your buyers, your Telegram bot, your prices and your team.",
      points: ["Your own private CRM and deal board", "An assistant that answers buyers for you", "Invite your team and set their roles"],
      after: "After you confirm your email you start your agency (you become its owner) or accept an invitation to join one.",
    },
  } as const;
  const role = ROLES[kind];

  return (
    <main className="grid min-h-screen md:grid-cols-2">
      <section className="relative hidden flex-col justify-between overflow-hidden border-r border-line bg-gradient-to-br from-[#f3e7d3] via-[#ead7b4] to-[#d3b27a] p-12 md:flex">
        <Link href="/" className="flex items-center gap-3"><BrandMark className="h-10 w-10" /><span className="display text-xl font-medium">PropPulse AI</span></Link>
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-[#5c4119]">{kind === "agency" ? "For brokers and agencies" : "For home seekers"}</p>
          <h2 className="display mt-3 text-5xl font-medium leading-[1.08]">{kind === "agency" ? <>Every buyer answered,<br /><span className="italic text-[#5c4119]">while you sleep.</span></> : <>Find the right home,<br /><span className="italic text-[#5c4119]">with someone who answers.</span></>}</h2>
          <ul className="mt-6 space-y-3 text-[15px] text-[#4a3716]">
            {role.points.map((p) => <li key={p} className="flex gap-2"><span aria-hidden="true">✓</span><span>{p}</span></li>)}
          </ul>
        </div>
        <p className="text-xs text-[#4a3716]/80">Addis Ababa real estate, with a real broker behind every chat.</p>
      </section>
      <div className="flex items-center justify-center px-4 py-10">
        <div className="card w-full max-w-md rounded-2xl border border-line bg-panel p-8 shadow-[0_30px_60px_-30px_rgb(60_40_10/0.35)]">
          <Link href="/" className="mb-4 inline-block md:hidden"><BrandMark /></Link>
          <p className="text-xs font-semibold uppercase tracking-widest text-accent">PropPulse AI</p>
          {sent ? (
            <>
              <h1 className="display mt-1 text-3xl font-medium">Check your email</h1>
              <p role="status" className="mt-3 text-sm text-muted">We sent a confirmation link to <span className="font-medium text-ink">{email}</span>. Open it on this device to finish creating your account.</p>
              <ol className="mt-4 space-y-2 text-sm text-muted">
                <li><span className="font-semibold text-ink">1.</span> Open the email and click the link.</li>
                <li><span className="font-semibold text-ink">2.</span> Sign in with your email and password.</li>
                <li><span className="font-semibold text-ink">3.</span> {kind === "agency" ? "Name your agency and you are in your console." : "Start browsing homes."}</li>
              </ol>
              <p className="mt-4 text-xs text-muted">No email after a few minutes? Check spam, or wait a little and try again.</p>
              <p className="mt-4 text-sm text-muted">Already confirmed? <Link href={signInHref} className="font-medium text-accent hover:underline">Sign in</Link></p>
            </>
          ) : (
            <form onSubmit={submit}>
              <h1 className="display mt-1 text-3xl font-medium">Create your account</h1>
              <p className="mt-1 text-sm text-muted">First, tell us what brings you here.</p>
              <div role="radiogroup" aria-label="What brings you here" className="mt-4 grid gap-2 sm:grid-cols-2">
                {(["buyer", "agency"] as const).map((value) => (
                  <button key={value} type="button" role="radio" aria-checked={kind === value} onClick={() => setKind(value)}
                    className={`rounded-lg border px-3 py-3 text-left text-sm transition ${kind === value ? "border-accent bg-accent/10 ring-2 ring-accent/30" : "border-line text-muted hover:border-accent/50 hover:text-ink"}`}>
                    <span className="block font-semibold text-ink">{ROLES[value].title}</span>
                  </button>
                ))}
              </div>
              <p className="mt-3 text-sm text-muted">{role.lead}</p>
              <label htmlFor="name" className="mt-6 block text-sm font-medium">Full name</label>
              <input id="name" name="name" autoFocus autoComplete="name" required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} className={field} />
              <label htmlFor="email" className="mt-4 block text-sm font-medium">Email</label>
              <input id="email" name="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} className={field} />
              <label htmlFor="password" className="mt-4 block text-sm font-medium">Password</label>
              <div className="relative">
                <input id="password" name="password" type={show ? "text" : "password"} autoComplete="new-password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} className={`${field} pr-16`} />
                <button type="button" onClick={() => setShow(!show)} aria-pressed={show} className="absolute right-3 top-1/2 mt-0.5 -translate-y-1/2 text-xs font-medium text-muted hover:text-ink">{show ? "Hide" : "Show"}</button>
              </div>
              <p className={`mt-1 text-xs ${password.length >= 8 ? "text-good" : "text-muted"}`}>{password.length >= 8 ? "Good, that is long enough." : "At least 8 characters."}</p>
              {!supabase && <p role="alert" className="mt-3 text-sm text-warn">Sign-up is not set up on this server yet.</p>}
              {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}
              <button type="submit" disabled={busy || !supabase || !name.trim() || !email || !password} className="mt-5 w-full rounded-md bg-accent px-3 py-2.5 text-sm font-semibold text-white transition hover:brightness-110 disabled:opacity-50">
                {busy ? "Creating..." : kind === "agency" ? "Create my broker account" : "Create account"}
              </button>
              <p className="mt-3 text-xs text-muted">{role.after}</p>
              <p className="mt-5 text-sm text-muted">Already have an account? <Link href={signInHref} className="font-medium text-accent hover:underline">Sign in</Link></p>
            </form>
          )}
        </div>
      </div>
    </main>
  );
}
