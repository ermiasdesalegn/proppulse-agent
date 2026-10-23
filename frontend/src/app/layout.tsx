import type { Metadata, Viewport } from "next";
import Backdrop from "@/components/Backdrop";
import "./globals.css";

export const metadata: Metadata = {
  title: "PropPulse Command Center",
  description: "Broker console for the PropPulse AI agent",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body><Backdrop />{children}</body>
    </html>
  );
}
