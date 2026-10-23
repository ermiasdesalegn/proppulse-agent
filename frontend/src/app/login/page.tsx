"use client";

import { useState } from "react";
import { Logo } from "@/components/icons";

export default function LoginPage() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/auth/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ password }) });
      if (res.ok) {
        window.location.href = "/";
        return;
      }
      setError((await res.json().catch(() => null))?.detail ?? "Sign-in failed.");
    } catch {
      setError("Could not reach the dashboard server.");
    }
    setBusy(false);
  }

  return (
    <main className="grid min-h-screen md:grid-cols-2">
      <section className="relative hidden flex-col justify-between overflow-hidden border-r border-white/[0.07] p-12 md:flex">
        <div className="flex items-center gap-3"><Logo className="h-10 w-10" /><span className="text-sm font-semibold uppercase tracking-widest">PropPulse AI</span></div>
        <div>
          <h2 className="text-4xl font-semibold leading-tight tracking-tight">Every buyer answered.<br /><span className="text-accent">Every deal in view.</span></h2>
          <ul className="mt-6 space-y-3 text-sm text-muted">
            <li>Telegram agent in English and Amharic</li>
            <li>Live view of every step the agent takes</li>
            <li>Site visits, routes and commissions in one place</li>
          </ul>
        </div>
        <p className="text-xs text-muted">Addis Ababa real estate, run by one broker and one agent.</p>
      </section>
      <div className="flex items-center justify-center px-4 py-10">
        <form onSubmit={submit} className="card w-full max-w-sm rounded-md border border-line bg-panel p-7">
          <div className="mb-4 md:hidden"><Logo /></div>
          <p className="text-xs font-semibold uppercase tracking-widest text-accent">PropPulse AI</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">Command Center</h1>
          <p className="mt-1 text-sm text-muted">Sign in to manage buyers, deals and the agent.</p>
          <label htmlFor="password" className="mt-6 block text-sm font-medium">Password</label>
          <input
            id="password" name="password" type="password" autoFocus autoComplete="current-password" required
            value={password} onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-md border border-line bg-bg px-3 py-2.5 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/30"
          />
          {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}
          <button type="submit" disabled={busy || !password} className="mt-5 w-full rounded-md bg-accent px-3 py-2.5 text-sm font-semibold text-black transition hover:brightness-110 disabled:opacity-50">
            {busy ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </div>
    </main>
  );
}
