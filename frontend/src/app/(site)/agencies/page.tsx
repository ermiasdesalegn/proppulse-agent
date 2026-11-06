import type { Metadata } from "next";
import Link from "next/link";
import { BrandMark } from "@/components/site/SiteChrome";
import { safeLogo } from "@/lib/agencysite";
import { loadDirectory } from "@/lib/portal";

export const metadata: Metadata = { title: "Agencies on PropPulse AI", description: "The agencies on PropPulse AI that chose to be listed." };
export const dynamic = "force-dynamic";

// Only agencies that turned the listing on appear here (the backend decides); every agency is still reachable at its own address.
export default async function AgenciesPage() {
  const agencies = await loadDirectory();
  return (
    <main className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
      <h1 className="display text-3xl font-medium">Agencies</h1>
      <p className="mt-2 text-sm text-muted">Pick an agency to see its homes, its prices and its assistant.</p>
      {agencies.length === 0 ? (
        <p className="mt-8 text-sm text-muted">No agency has chosen to be listed yet.</p>
      ) : (
        <ul className="mt-8 grid gap-3 sm:grid-cols-2">
          {agencies.map((a) => {
            const logo = safeLogo(a.logo_url);
            return (
              <li key={a.slug}>
                <Link href={`/${a.slug}`} className="flex items-center gap-3 rounded-lg border border-line bg-raised/60 p-4 transition hover:border-accent">
                  {/* eslint-disable-next-line @next/next/no-img-element -- an agency's own picture, from any https address */}
                  {logo ? <img src={logo} alt="" className="h-10 w-10 rounded-md object-contain" /> : <BrandMark className="h-10 w-10" />}
                  <span className="display text-lg font-medium">{a.name}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </main>
  );
}
