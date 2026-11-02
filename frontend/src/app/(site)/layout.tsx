import { notFound } from "next/navigation";
import { colorChannels } from "@/lib/agencysite";
import { siteFonts } from "@/lib/fonts";
import { loadAgency } from "@/lib/portal";
import { currentAgency } from "@/lib/portal-server";

export const dynamic = "force-dynamic"; // the agency comes from the address of each request

// The public site: a light, warm theme (see .site in globals.css) around the landing page, sign-in and sign-up, in the colour of the agency
// the visitor chose. An address that names an agency the backend does not know (or has suspended) is the usual "not found" page.
export default async function SiteLayout({ children }: { children: React.ReactNode }) {
  const slug = currentAgency();
  const agency = await loadAgency(slug);
  if (agency === "not-found") notFound();
  const accent = agency ? colorChannels(agency.brand_color) : null;
  return <div className={`site ${siteFonts}`} style={accent ? ({ "--accent": accent, "--accent2": accent } as React.CSSProperties) : undefined}>{children}</div>;
}
