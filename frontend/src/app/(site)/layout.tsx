import { siteFonts } from "@/lib/fonts";

// The public site: a light, warm theme (see .site in globals.css) around the landing page, sign-in and sign-up.
export default function SiteLayout({ children }: { children: React.ReactNode }) {
  return <div className={`site ${siteFonts}`}>{children}</div>;
}
