"use client";

import { motion } from "framer-motion";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
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
    { href: "/projects", icon: "listing", label: "Projects" },
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
  const router = useRouter();
  // The clicked link lights up at once; the page follows when it is ready. useTransition keeps the sidebar responsive while the next page loads.
  const [target, setTarget] = useState<string | null>(null);
  const [navigating, startNavigation] = useTransition();
  useEffect(() => { if (!navigating) setTarget(null); }, [navigating, pathname]);
  // Ahead of the click: fetch every page's code (a production build does this on its own for visible links; this also covers the rest).
  useEffect(() => { for (const g of GROUPS) for (const i of g.items) router.prefetch(i.href); }, [router]);
  const current = target ?? pathname;
  const here = GROUPS.flatMap((g) => g.items).find((i) => (i.href === "/" ? pathname === "/" : pathname.startsWith(i.href)));
  useEffect(() => { document.title = here ? `${here.label} | PropPulse AI` : pathname.startsWith("/tours") ? "Pre-tour dossier | PropPulse AI" : "PropPulse AI | Command Center"; }, [here, pathname]);

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
                  const active = item.href === "/" ? current === "/" : current.startsWith(item.href);
                  return (
                    <Link
                      key={item.href} href={item.href} prefetch aria-current={active ? "page" : undefined}
                      onClick={(e) => {
                        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return; // let "open in a new tab" work as usual
                        e.preventDefault();
                        if (item.href === pathname) return;
                        setTarget(item.href);
                        startNavigation(() => router.push(item.href));
                      }}
                      className={clsx("group relative flex items-center gap-3 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition-colors", active ? "bg-white/5 text-white" : "text-muted hover:bg-white/[0.04] hover:text-white")}
                    >
                      {active && <motion.span layoutId="nav-bar" transition={{ type: "spring", stiffness: 500, damping: 40 }} className="absolute inset-y-1.5 left-0 hidden w-[3px] rounded-r bg-white/50 md:block" />}
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

        <div className="relative flex min-w-0 flex-1 flex-col">
          {navigating && <div role="progressbar" aria-label="Loading the page" className="fixed inset-x-0 top-0 z-50 h-0.5 overflow-hidden bg-white/5"><div className="nav-progress h-full w-1/3 bg-white/60" /></div>}
          <ControlBar />
          <motion.main key={pathname} className="mx-auto w-full max-w-[1500px] flex-1 p-4 md:p-8" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.15 }}>
            {children}
          </motion.main>
        </div>
      </div>
    </LiveProvider>
  );
}
