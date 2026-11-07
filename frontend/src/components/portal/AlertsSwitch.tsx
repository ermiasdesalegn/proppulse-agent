"use client";

import { useState } from "react";

export default function AlertsSwitch({ enabled }: { enabled: boolean }) {
  const [on, setOn] = useState(enabled);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  async function toggle() {
    if (busy) return;
    const next = !on;
    setBusy(true);
    setError(false);
    try {
      const res = await fetch("/api/me/alerts", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ enabled: next }) });
      if (res.status === 401) { window.location.href = "/login"; return; }
      if (!res.ok) throw new Error("failed");
      setOn(((await res.json()) as { enabled: boolean }).enabled);
    } catch {
      setError(true);
    }
    setBusy(false);
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-line bg-white px-6 py-5">
      <div>
        <p className="font-semibold">Price-drop and new-listing alerts</p>
        <p className="text-sm text-muted" aria-live="polite">{on ? "On: we message you about homes that suit you." : "Off: you will not receive these alerts."}</p>
        {error && <p role="alert" className="mt-1 text-sm text-[#b42318]">We could not save that. Please try again.</p>}
      </div>
      <button type="button" role="switch" aria-checked={on} aria-label="Price-drop and new-listing alerts" onClick={toggle} disabled={busy}
        className={`relative h-7 w-12 rounded-full transition disabled:opacity-60 ${on ? "bg-accent" : "bg-ink/25"}`}>
        <span className={`absolute top-0.5 h-6 w-6 rounded-full bg-white shadow transition-all ${on ? "left-[1.4rem]" : "left-0.5"}`} />
      </button>
    </div>
  );
}
