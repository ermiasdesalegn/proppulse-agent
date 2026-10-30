import Link from "next/link";
import AccountLink from "./AccountLink";

export function BrandMark({ className = "h-9 w-9" }: { className?: string }) {
  // eslint-disable-next-line @next/next/no-img-element -- a small fixed SVG
  return <img src="/logo.svg" alt="" className={className} />;
}

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-line/70 bg-bg/85 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
        <Link href="/" className="flex items-center gap-2.5" aria-label="PropPulse AI, home">
          <BrandMark />
          <span className="display text-xl font-medium">PropPulse <span className="text-accent">AI</span></span>
        </Link>
        <nav aria-label="Main" className="hidden items-center gap-8 text-sm font-medium text-muted md:flex">
          <Link href="/#homes" className="transition hover:text-ink">Featured homes</Link>
          <Link href="/#how" className="transition hover:text-ink">How it works</Link>
          <Link href="/#assistant" className="transition hover:text-ink">The assistant</Link>
          <Link href="/#areas" className="transition hover:text-ink">Areas</Link>
        </nav>
        <div className="flex items-center gap-2"><AccountLink /></div>
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="border-t border-line bg-raised/60">
      <div className="mx-auto grid max-w-7xl gap-10 px-4 py-14 sm:px-6 md:grid-cols-[1.4fr_1fr_1fr] lg:px-8">
        <div>
          <div className="flex items-center gap-2.5"><BrandMark className="h-8 w-8" /><span className="display text-lg font-medium">PropPulse AI</span></div>
          <p className="mt-4 max-w-sm text-sm leading-relaxed text-muted">Homes from Addis Ababa&apos;s developers and property portals in one place, with an assistant that answers at any hour and a broker who takes over when it matters.</p>
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-ink/60">Explore</p>
          <ul className="mt-4 space-y-2.5 text-sm text-muted">
            <li><Link href="/#homes" className="hover:text-ink">Featured homes</Link></li>
            <li><Link href="/#areas" className="hover:text-ink">Browse by area</Link></li>
            <li><Link href="/#how" className="hover:text-ink">How it works</Link></li>
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
        <p className="mx-auto max-w-7xl px-4 py-5 text-xs text-muted sm:px-6 lg:px-8">&copy; {new Date().getFullYear()} PropPulse AI &middot; Addis Ababa, Ethiopia. Prices and availability come from the developers and can change; ask the assistant or the broker to confirm before you decide.</p>
      </div>
    </footer>
  );
}
