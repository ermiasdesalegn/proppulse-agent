"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LiveProvider, useLive, type LiveStatus } from "@/lib/live";
import clsx from "./clsx";
import ControlBar from "./ControlBar";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/crm", label: "CRM & Takeover" },
  { href: "/board", label: "Deal Board" },
  { href: "/brain", label: "Agent Brain" },
  { href: "/map", label: "Map & Tours" },
  { href: "/studio", label: "Studio" },
  { href: "/chat", label: "Chat Simulator" },
];

const STATUS: Record<LiveStatus, { dot: string; text: string }> = {
  live: { dot: "bg-good", text: "Live" },
  connecting: { dot: "bg-warn", text: "Connecting" },
  reconnecting: { dot: "bg-bad", text: "Reconnecting" },
};

function LiveBadge() {
  const { status } = useLive();
  return (
    <p className="flex items-center gap-2 text-xs text-muted" role="status">
      <span className={clsx("h-2 w-2 rounded-full", STATUS[status].dot)} />
      {STATUS[status].text}
    </p>
  );
}

export default function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  async function signOut() {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.href = "/login";
  }

  return (
    <LiveProvider>
      <div className="flex min-h-screen flex-col md:flex-row">
        <aside className="shrink-0 border-b border-line bg-panel md:sticky md:top-0 md:h-screen md:w-56 md:border-b-0 md:border-r">
          <div className="flex items-center justify-between px-4 py-3 md:block md:py-5">
            <div>
              <p className="text-xs font-semibold uppercase tracking-widest text-accent">PropPulse AI</p>
              <p className="text-sm font-semibold">Command Center</p>
            </div>
            <div className="md:mt-3"><LiveBadge /></div>
          </div>
          <nav aria-label="Sections" className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:overflow-visible md:pb-0">
            {NAV.map((item) => {
              const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href} href={item.href} aria-current={active ? "page" : undefined}
                  className={clsx("whitespace-nowrap rounded-md px-3 py-2 text-sm font-medium", active ? "bg-accent/10 text-accent" : "text-muted hover:bg-raised hover:text-ink")}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="hidden px-4 py-4 md:absolute md:bottom-0 md:block">
            <button onClick={signOut} className="text-sm text-muted hover:text-ink">Sign out</button>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <ControlBar />
          <main className="flex-1 p-4 md:p-6">{children}</main>
        </div>
      </div>
    </LiveProvider>
  );
}
