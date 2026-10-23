"use client";

import { useState } from "react";

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
    <main className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-xl border border-line bg-panel p-6 shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-widest text-accent">PropPulse AI</p>
        <h1 className="mt-1 text-xl font-semibold">Command Center</h1>
        <p className="mt-1 text-sm text-muted">Sign in to manage buyers, deals and the agent.</p>
        <label htmlFor="password" className="mt-5 block text-sm font-medium">Password</label>
        <input
          id="password" name="password" type="password" autoFocus autoComplete="current-password" required
          value={password} onChange={(e) => setPassword(e.target.value)}
          className="mt-1 w-full rounded-md border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/30"
        />
        {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}
        <button type="submit" disabled={busy || !password} className="mt-5 w-full rounded-md bg-accent px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">
          {busy ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </main>
  );
}
