const base = "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full px-5 py-2.5 text-sm font-semibold transition focus-visible:outline-2";

const variants = {
  primary: "bg-ink text-white hover:bg-ink/85",
  brass: "bg-accent text-white hover:brightness-110",
  outline: "border border-ink/20 bg-white/60 text-ink hover:border-ink/50 hover:bg-white",
  light: "bg-white text-ink hover:bg-white/90",
  ghost: "text-ink hover:bg-ink/5",
} as const;

export function buttonClass(variant: keyof typeof variants = "primary", extra = ""): string {
  return `${base} ${variants[variant]} ${extra}`.trim();
}
