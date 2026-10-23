import type { Config } from "tailwindcss";

const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
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
