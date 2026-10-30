import type { Metadata } from "next";
import Link from "next/link";
import PortalNav from "@/components/portal/PortalNav";
import { BrandMark } from "@/components/site/SiteChrome";
import { siteFonts } from "@/lib/fonts";
import { signedInBuyer } from "@/lib/portal-server";

export const metadata: Metadata = { title: { default: "Client Portal", template: "%s | PropPulse AI" } };
export const dynamic = "force-dynamic"; // reads the signed-in buyer on every request

export default async function PortalLayout({ children }: { children: React.ReactNode }) {
  const buyer = await signedInBuyer();
  return (
    <div className={`site ${siteFonts} flex min-h-screen flex-col`}>
      <header className="sticky top-0 z-40 border-b border-line/70 bg-bg/90 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-3 sm:px-6 lg:px-8">
          <Link href="/" className="flex items-center gap-2.5" aria-label="PropPulse AI, home">
            <BrandMark className="h-8 w-8" />
            <span className="display text-lg font-medium">PropPulse <span className="text-accent">AI</span></span>
          </Link>
          <PortalNav email={buyer?.email ?? null} />
        </div>
      </header>
      {!buyer && (
        <p className="border-b border-warn/30 bg-warn/10 px-4 py-2 text-center text-sm text-warn" role="status">
          You are signed in as the broker, which has no buyer account, so listings cannot load here. <Link href="/console" className="font-semibold underline">Back to the console</Link>
        </p>
      )}
      <div className="mx-auto w-full max-w-7xl flex-1 px-4 py-8 sm:px-6 lg:px-8">{children}</div>
      <footer className="border-t border-line">
        <p className="mx-auto max-w-7xl px-4 py-5 text-xs text-muted sm:px-6 lg:px-8">Prices and availability come from the developers and can change. Ask our broker to confirm before you decide.</p>
      </footer>
    </div>
  );
}
