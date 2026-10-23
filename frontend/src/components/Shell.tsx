"use client";

import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LiveProvider, useLive, type LiveStatus } from "@/lib/live";
import clsx from "./clsx";
import ControlBar from "./ControlBar";
import { Icon, Logo, type IconName } from "./icons";

const NAV: { href: string; label: string; icon: IconName }[] = [
  { href: "/", icon: "overview", label: "Overview" },
  { href: "/crm", icon: "crm", label: "CRM & Takeover" },
  { href: "/board", icon: "board", label: "Deal Board" },
  { href: "/brain", icon: "brain", label: "Agent Brain" },
  { href: "/map", icon: "map", label: "Map & Tours" },
  { href: "/analytics", icon: "analytics", label: "Analytics" },
  { href: "/studio", icon: "studio", label: "Studio" },
  { href: "/chat", icon: "chat", label: "Chat Simulator" },
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
        <aside className="shrink-0 border-b border-[#26264a] bg-[#0c0c20] md:sticky md:top-0 md:h-screen md:w-56 md:border-b-0 md:border-r md:border-[#26264a]">
          <div className="flex items-center justify-between px-4 py-3 md:block md:py-5">
            <div className="flex items-center gap-3">
              <Logo />
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-widest text-muted">PropPulse AI</p>
                <p className="text-sm font-semibold leading-tight">Command Center</p>
              </div>
            </div>
            <div className="md:mt-4"><LiveBadge /></div>
          </div>
          <nav aria-label="Sections" className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:overflow-visible md:px-3 md:pb-0">
            {NAV.map((item) => {
              const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href} href={item.href} aria-current={active ? "page" : undefined}
                  className={clsx("group relative flex items-center gap-3 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition-colors", active ? "bg-white/[0.06] text-white" : "text-muted hover:bg-[#12122a] hover:text-white")}
                >
                  {active && <motion.span layoutId="nav-bar" transition={{ type: "spring", stiffness: 500, damping: 40 }} className="absolute inset-y-1.5 left-0 hidden w-[3px] rounded-r bg-accent md:block" />}
                  <span className={active ? "text-accent" : ""}><Icon name={item.icon} /></span>
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="hidden px-3 py-4 md:absolute md:bottom-0 md:block md:w-56">
            <button onClick={signOut} className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted transition hover:bg-[#12122a] hover:text-white"><Icon name="signout" />Sign out</button>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <ControlBar />
          <AnimatePresence mode="wait" initial={false}>
            <motion.main key={pathname} className="flex-1 p-4 md:p-6" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.12 }}>
              {children}
            </motion.main>
          </AnimatePresence>
        </div>
      </div>
    </LiveProvider>
  );
}
