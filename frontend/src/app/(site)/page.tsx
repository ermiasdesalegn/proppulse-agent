import type { Metadata } from "next";
import { Areas, Assistant, FeaturedHomes, FinalCta, Hero, HowItWorks, TrustStrip } from "@/components/site/Landing";
import { SiteFooter, SiteHeader } from "@/components/site/SiteChrome";
import { loadFeatured } from "@/lib/portal";

// Rendered per request, with the featured homes cached for a minute by the fetch itself (lib/portal.ts). A prerendered page would keep
// whatever the build could reach: if the backend was down then, the empty fallback would be baked into the page.
export const dynamic = "force-dynamic";

const DESCRIPTION = "Browse homes from Addis Ababa's developers and property portals in one place, and ask an assistant that answers in English and Amharic, backed by a real broker.";

export const metadata: Metadata = {
  title: { absolute: "PropPulse AI | Find a home in Addis Ababa" },
  description: DESCRIPTION,
  robots: { index: true, follow: true },
  openGraph: { title: "PropPulse AI | Find a home in Addis Ababa", description: DESCRIPTION, type: "website", locale: "en_ET" },
};

export default async function LandingPage() {
  const featured = await loadFeatured();
  return (
    <>
      <SiteHeader />
      <main>
        <Hero unit={featured?.units[0]} />
        <TrustStrip stats={featured?.stats ?? null} />
        <FeaturedHomes units={featured?.units ?? []} />
        <HowItWorks />
        <Assistant />
        <Areas areas={(featured?.sub_cities ?? []).slice(0, 8)} />
        <FinalCta />
      </main>
      <SiteFooter />
    </>
  );
}
