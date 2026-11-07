"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { SignOutButton } from "./PortalControls";

const ITEMS = [
  { href: "/portal", label: "Browse", match: (p: string) => p === "/portal" || p.startsWith("/portal/homes") },
  { href: "/portal/map", label: "Map", match: (p: string) => p.startsWith("/portal/map") },
  { href: "/portal/saved", label: "Saved", match: (p: string) => p.startsWith("/portal/saved") },
  { href: "/portal/chat", label: "Concierge", match: (p: string) => p.startsWith("/portal/chat") },
];

export default function PortalNav({ email }: { email: string | null }) {
  const pathname = usePathname();
  return (
    <>
      <nav aria-label="Portal" className="flex items-center gap-1 text-sm font-medium">
        {ITEMS.map((i) => {
          const active = i.match(pathname);
          return (
            <Link key={i.href} href={i.href} aria-current={active ? "page" : undefined}
              className={`rounded-full px-4 py-2 transition ${active ? "bg-ink text-white" : "text-muted hover:bg-ink/5 hover:text-ink"}`}>{i.label}</Link>
          );
        })}
      </nav>
      <div className="flex items-center gap-3 text-sm">
        {email && <span className="hidden max-w-[11rem] truncate text-muted lg:inline" title={email}>{email}</span>}
        {email && (
          <Link href="/start" className="rounded-full border border-line px-3 py-1.5 text-xs font-semibold text-accent transition hover:bg-accent/10"
            title="You are signed in as a buyer. Brokers and agencies start their own workspace here.">
            Are you a broker? Start an agency
          </Link>
        )}
        <SignOutButton className="rounded-full px-3 py-2 font-medium text-muted transition hover:bg-ink/5 hover:text-ink" />
      </div>
    </>
  );
}
