import localFont from "next/font/local";

// Fraunces for headings and prices, Inter for everything else. Both are open-licence variable fonts (SIL OFL, see src/fonts) kept in the
// repository, so a build never depends on Google being reachable and visitors' browsers never contact Google. Latin only; Amharic text
// uses the reader's system Ethiopic font.
const display = localFont({
  src: [
    { path: "../fonts/fraunces-latin-wght-normal.woff2", style: "normal", weight: "100 900" },
    { path: "../fonts/fraunces-latin-wght-italic.woff2", style: "italic", weight: "100 900" },
  ],
  variable: "--font-display",
  display: "swap",
});
const sans = localFont({
  src: [{ path: "../fonts/inter-latin-wght-normal.woff2", style: "normal", weight: "100 900" }],
  variable: "--font-sans",
  display: "swap",
});

export const siteFonts = `${display.variable} ${sans.variable}`;
