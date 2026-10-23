import clsx from "./clsx";
import { Icon, type IconName } from "./icons";

export function Card({ title, action, children, className }: { title?: React.ReactNode; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={clsx("card rounded-2xl border", className)}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-white/[0.07] px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

const TONES = {
  neutral: "bg-raised text-muted border-line",
  accent: "bg-accent/10 text-accent border-accent/30",
  good: "bg-good/10 text-good border-good/30",
  warn: "bg-warn/10 text-warn border-warn/30",
  bad: "bg-bad/10 text-bad border-bad/30",
} as const;

export function Pill({ tone = "neutral", children, title }: { tone?: keyof typeof TONES; children: React.ReactNode; title?: string }) {
  return <span title={title} className={clsx("inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap", TONES[tone])}>{children}</span>;
}

export function Stat({ label, value, hint, icon }: { label: string; value: React.ReactNode; hint?: string; icon?: IconName }) {
  return (
    <div className="card group relative overflow-hidden rounded-2xl border px-4 py-4 transition duration-300 hover:-translate-y-0.5 hover:shadow-[0_0_32px_-8px_rgb(var(--accent)/0.5)]">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/70 to-transparent" />
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-medium uppercase tracking-wide text-muted">{label}</p>
        {icon && <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-accent/10 text-accent"><Icon name={icon} /></span>}
      </div>
      <p className="tnum mt-2 text-3xl font-semibold tracking-tight">{value}</p>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="px-4 py-8 text-center text-sm text-muted">{children}</p>;
}

export function ErrorNote({ children }: { children: React.ReactNode }) {
  return <p role="alert" className="rounded-md border border-bad/30 bg-bad/10 px-3 py-2 text-sm text-bad">{children}</p>;
}

export function Button({ variant = "default", className, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "primary" | "danger" | "ghost" | "glow" }) {
  const styles = {
    default: "border border-white/10 bg-white/[0.03] text-ink/90 backdrop-blur hover:border-white/20 hover:bg-white/[0.07]",
    glow: "glow-border bg-accent/[0.07] text-ink backdrop-blur hover:bg-accent/[0.14]",
    primary: "btn-gradient text-white shadow-[0_0_20px_-6px_rgb(var(--accent)/0.7)] hover:opacity-90",
    danger: "border border-bad/40 bg-bad/10 text-bad hover:bg-bad/20",
    ghost: "hover:bg-raised",
  } as const;
  return (
    <button
      {...props}
      className={clsx("rounded-lg px-3.5 py-1.5 text-sm font-medium transition duration-200 active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent", styles[variant], className)}
    />
  );
}

export const DEVELOPER_COLOR: Record<string, string> = { ayat: "text-ayat", noah: "text-noah", gift: "text-gift" };
export const DEVELOPER_BG: Record<string, string> = { ayat: "bg-ayat", noah: "bg-noah", gift: "bg-gift" };
export const DEVELOPER_VAR: Record<string, string> = { ayat: "var(--ayat)", noah: "var(--noah)", gift: "var(--gift)" };

export function ScoreBar({ score }: { score: number }) {
  const tone = score >= 70 ? "bg-good" : score >= 40 ? "bg-warn" : "bg-muted";
  return (
    <div className="flex items-center gap-2" title={`Lead score ${score} of 100`}>
      <div className="h-1.5 w-14 overflow-hidden rounded-full bg-raised"><div className={clsx("h-full", tone)} style={{ width: `${Math.min(100, score)}%` }} /></div>
      <span className="tnum text-xs text-muted">{score}</span>
    </div>
  );
}
