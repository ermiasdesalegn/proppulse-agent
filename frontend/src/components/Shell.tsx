"use client";

import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LiveProvider, useLive, type LiveStatus } from "@/lib/live";
import clsx from "./clsx";
import ControlBar from "./ControlBar";
import { Icon, Logo, type IconName } from "./icons";
import { Avatar } from "./ui";

const GROUPS: { label: string; items: { href: string; label: string; icon: IconName }[] }[] = [
  { label: "Workspace", items: [
    { href: "/", icon: "overview", label: "Overview" },
    { href: "/crm", icon: "crm", label: "CRM & Takeover" },
    { href: "/board", icon: "board", label: "Deal Board" },
    { href: "/map", icon: "map", label: "Map & Tours" },
  ] },
  { label: "Intelligence", items: [
    { href: "/brain", icon: "brain", label: "Agent Brain" },
    { href: "/analytics", icon: "analytics", label: "Analytics" },
  ] },
  { label: "Tools", items: [
    { href: "/studio", icon: "studio", label: "Studio" },
    { href: "/chat", icon: "chat", label: "Chat Simulator" },
  ] },
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
        <aside className="shrink-0 border-b border-[#222] bg-[#0f0f0f] md:sticky md:top-0 md:h-screen md:w-56 md:border-b-0 md:border-r md:border-[#222]">
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
          <nav aria-label="Sections" className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:gap-0 md:overflow-visible md:px-3 md:pb-0">
            {GROUPS.map((group) => (
              <div key={group.label} className="flex gap-1 md:mb-4 md:flex-col">
                <p className="hidden px-3 pb-1.5 text-[11px] font-semibold uppercase tracking-widest text-muted/70 md:block">{group.label}</p>
                {group.items.map((item) => {
                  const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
                  return (
                    <Link
                      key={item.href} href={item.href} aria-current={active ? "page" : undefined}
                      className={clsx("group relative flex items-center gap-3 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition-colors", active ? "bg-accent/10 text-accent" : "text-muted hover:bg-white/[0.04] hover:text-white")}
                    >
                      {active && <motion.span layoutId="nav-bar" transition={{ type: "spring", stiffness: 500, damping: 40 }} className="absolute inset-y-1.5 left-0 hidden w-[3px] rounded-r bg-accent md:block" />}
                      <Icon name={item.icon} />
                      {item.label}
                    </Link>
                  );
                })}
              </div>
            ))}
          </nav>
          <div className="hidden border-t border-[#222] p-3 md:absolute md:bottom-0 md:block md:w-56">
            <div className="flex items-center gap-3 rounded-lg px-2 py-1.5">
              <Avatar name="Broker" className="h-8 w-8" />
              <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">Broker</p><p className="truncate text-xs text-muted">Addis Ababa</p></div>
              <button onClick={signOut} aria-label="Sign out" title="Sign out" className="rounded-md p-1.5 text-muted transition hover:bg-[#161616] hover:text-white"><Icon name="signout" /></button>
            </div>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <ControlBar />
          <AnimatePresence mode="wait" initial={false}>
            <motion.main key={pathname} className="mx-auto w-full max-w-[1500px] flex-1 p-4 md:p-8" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.12 }}>
              {children}
            </motion.main>
          </AnimatePresence>
        </div>
      </div>
    </LiveProvider>
  );
}
