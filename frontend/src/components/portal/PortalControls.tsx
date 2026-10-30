"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function HeartIcon({ filled, className = "h-5 w-5" }: { filled: boolean; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 20.5s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.6a4.3 4.3 0 0 1 7.5 2.7c0 5.6-7.5 10.2-7.5 10.2Z" />
    </svg>
  );
}

/** Save or unsave a home. The heart changes at once; if the server refuses, it changes back. */
export function SaveButton({ id, saved, label = false }: { id: string; saved: boolean; label?: boolean }) {
  const router = useRouter();
  const [on, setOn] = useState(saved);
  const [busy, setBusy] = useState(false);

  async function toggle() {
    if (busy) return;
    const next = !on;
    setOn(next);
    setBusy(true);
    try {
      const res = await fetch(`/api/me/saved/${id}`, { method: next ? "PUT" : "DELETE" });
      if (res.status === 401) { window.location.href = "/login"; return; }
      if (!res.ok) throw new Error("save failed");
      router.refresh(); // the lists and counts on the page are read on the server
    } catch {
      setOn(!next);
    }
    setBusy(false);
  }

  return label ? (
    <button type="button" onClick={toggle} aria-pressed={on} className={`inline-flex items-center justify-center gap-2 rounded-full border px-5 py-2.5 text-sm font-semibold transition ${on ? "border-accent bg-accent/10 text-accent" : "border-ink/20 bg-white text-ink hover:border-ink/50"}`}>
      <HeartIcon filled={on} className="h-4 w-4" />{on ? "Saved" : "Save this home"}
    </button>
  ) : (
    <button type="button" onClick={toggle} aria-pressed={on} aria-label={on ? "Remove from saved homes" : "Save this home"}
      className={`grid h-10 w-10 place-items-center rounded-full bg-white/95 shadow-md backdrop-blur transition hover:scale-105 ${on ? "text-[#c2410c]" : "text-ink/70 hover:text-ink"}`}>
      <HeartIcon filled={on} />
    </button>
  );
}

export function SignOutButton({ className = "", children = "Sign out" }: { className?: string; children?: React.ReactNode }) {
  async function out() {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.href = "/login?notice=signedout";
  }
  return <button type="button" onClick={out} className={className}>{children}</button>;
}
