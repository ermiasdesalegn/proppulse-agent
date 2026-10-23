import type { Config } from "tailwindcss";

const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    borderRadius: { none: "0", sm: "2px", DEFAULT: "3px", md: "4px", lg: "4px", xl: "4px", "2xl": "4px", "3xl": "4px", full: "2px" },
    extend: {
      colors: {
        bg: token("bg"),
        panel: token("panel"),
        raised: token("raised"),
        line: token("line"),
        ink: token("ink"),
        muted: token("muted"),
        accent: token("accent"),
        accent2: token("accent2"),
        good: token("good"),
        warn: token("warn"),
        bad: token("bad"),
        ayat: token("ayat"),
        noah: token("noah"),
        gift: token("gift"),
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "Helvetica", "Arial", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Cascadia Mono", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
