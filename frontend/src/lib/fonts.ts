import { Fraunces, Inter } from "next/font/google";

// Fraunces for headings and prices, Inter for everything else. Next downloads them once at build time and serves them from this site.
const display = Fraunces({ subsets: ["latin"], variable: "--font-display", display: "swap" });
const sans = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });

export const siteFonts = `${display.variable} ${sans.variable}`;
