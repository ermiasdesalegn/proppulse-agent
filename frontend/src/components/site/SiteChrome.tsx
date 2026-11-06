import Link from "next/link";
import { agencyHref, safeLogo, type AgencyCard } from "@/lib/agencysite";
import AccountLink from "./AccountLink";

export function BrandMark({ className = "h-9 w-9" }: { className?: string }) {
  // eslint-disable-next-line @next/next/no-img-element -- a small fixed SVG
  return <img src="/logo.svg" alt="" className={className} />;
}

/** The name and mark a visitor sees: the agency's own when they came to one, the platform's otherwise. */
function Brand({ agency, className }: { agency?: AgencyCard | null; className: string }) {
  const logo = safeLogo(agency?.logo_url);
  // eslint-disable-next-line @next/next/no-img-element -- an agency's own picture, from any https address
  if (logo) return <img src={logo} alt="" className={`${className} rounded-md object-contain`} />;
  return <BrandMark className={className} />;
}

export function SiteHeader({ agency }: { agency?: AgencyCard | null }) {
  const slug = agency?.slug ?? null;
  const home = (hash = "") => agencyHref(slug, `/${hash}`);
  return (
    <header className="sticky top-0 z-40 border-b border-line/70 bg-bg/85 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
        <Link href={home()} className="flex items-center gap-2.5" aria-label={`${agency?.name ?? "PropPulse AI"}, home`}>
          <Brand agency={agency} className="h-9 w-9" />
          {agency ? <span className="display text-xl font-medium">{agency.name}</span> : <span className="display text-xl font-medium">PropPulse <span className="text-accent">AI</span></span>}
        </Link>
        <nav aria-label="Main" className="hidden items-center gap-8 text-sm font-medium text-muted md:flex">
          <Link href={home("#homes")} className="transition hover:text-ink">Featured homes</Link>
          <Link href={home("#how")} className="transition hover:text-ink">How it works</Link>
          <Link href={home("#assistant")} className="transition hover:text-ink">The assistant</Link>
          <Link href={home("#areas")} className="transition hover:text-ink">Areas</Link>
        </nav>
        <div className="flex items-center gap-2"><AccountLink /></div>
      </div>
    </header>
  );
}

export function SiteFooter({ agency }: { agency?: AgencyCard | null }) {
  const name = agency?.name ?? "PropPulse AI";
  const home = (hash: string) => agencyHref(agency?.slug, `/${hash}`);
  return (
    <footer className="border-t border-line bg-raised/60">
      <div className="mx-auto grid max-w-7xl gap-10 px-4 py-14 sm:px-6 md:grid-cols-[1.4fr_1fr_1fr] lg:px-8">
        <div>
          <div className="flex items-center gap-2.5"><Brand agency={agency} className="h-8 w-8" /><span className="display text-lg font-medium">{name}</span></div>
          <p className="mt-4 max-w-sm text-sm leading-relaxed text-muted">Homes from Addis Ababa&apos;s developers and property portals in one place, with an assistant that answers at any hour and a broker who takes over when it matters.</p>
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-ink/60">Explore</p>
          <ul className="mt-4 space-y-2.5 text-sm text-muted">
            <li><Link href={home("#homes")} className="hover:text-ink">Featured homes</Link></li>
            <li><Link href={home("#areas")} className="hover:text-ink">Browse by area</Link></li>
            <li><Link href={home("#how")} className="hover:text-ink">How it works</Link></li>
            <li><Link href="/agencies" className="hover:text-ink">Other agencies</Link></li>
          </ul>
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-ink/60">Your account</p>
          <ul className="mt-4 space-y-2.5 text-sm text-muted">
            <li><Link href="/signup" className="hover:text-ink">Create an account</Link></li>
            <li><Link href="/login" className="hover:text-ink">Sign in</Link></li>
          </ul>
        </div>
      </div>
      <div className="border-t border-line">
        <p className="mx-auto max-w-7xl px-4 py-5 text-xs text-muted sm:px-6 lg:px-8">&copy; {new Date().getFullYear()} {name} &middot; Addis Ababa, Ethiopia. Prices and availability come from the developers and can change; ask the assistant or the broker to confirm before you decide.</p>
      </div>
    </footer>
  );
}
