import type { Metadata } from "next";
import { Areas, Assistant, FeaturedHomes, FinalCta, Hero, HowItWorks, TrustStrip } from "@/components/site/Landing";
import { SiteFooter, SiteHeader } from "@/components/site/SiteChrome";
import { loadAgency, loadFeatured } from "@/lib/portal";
import { currentAgency } from "@/lib/portal-server";

// Rendered per request, with the featured homes cached for a minute by the fetch itself (lib/portal.ts). A prerendered page would keep
// whatever the build could reach: if the backend was down then, the empty fallback would be baked into the page.
export const dynamic = "force-dynamic";

const DESCRIPTION = "Browse homes from Addis Ababa's developers and property portals in one place, and ask an assistant that answers in English and Amharic, backed by a real broker.";

const defaultMetadata: Metadata = {
  title: { absolute: "PropPulse AI | Find a home in Addis Ababa" },
  description: DESCRIPTION,
  robots: { index: true, follow: true },
  openGraph: { title: "PropPulse AI | Find a home in Addis Ababa", description: DESCRIPTION, type: "website", locale: "en_ET" },
};

// An agency's page carries its own name; the platform's page keeps the title above.
export async function generateMetadata(): Promise<Metadata> {
  const agency = await loadAgency(currentAgency());
  if (!agency || agency === "not-found" || !currentAgency()) return defaultMetadata;
  const title = `${agency.name} | Find a home in Addis Ababa`;
  return { ...defaultMetadata, title: { absolute: title }, openGraph: { ...defaultMetadata.openGraph, title } };
}

export default async function LandingPage() {
  const slug = currentAgency();
  const [featured, card] = await Promise.all([loadFeatured(slug), loadAgency(slug)]);
  const agency = card && card !== "not-found" && slug ? card : null; // the platform's own page keeps the platform's name
  return (
    <>
      <SiteHeader agency={agency} />
      <main>
        <Hero unit={featured?.hero ?? featured?.units[0]} />
        <TrustStrip stats={featured?.stats ?? null} />
        <FeaturedHomes units={featured?.units ?? []} />
        <HowItWorks />
        <Assistant />
        <Areas areas={(featured?.sub_cities ?? []).slice(0, 8)} />
        <FinalCta />
      </main>
      <SiteFooter agency={agency} />
    </>
  );
}
