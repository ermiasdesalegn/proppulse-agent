import clsx from "./clsx";
import { Icon, type IconName } from "./icons";

export function Card({ title, subtitle, action, children, className }: { title?: React.ReactNode; subtitle?: React.ReactNode; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={clsx("card rounded-xl border", className)}>
      {(title || action) && (
        <header className="flex items-start justify-between gap-3 px-5 pb-3 pt-4">
          <div className="min-w-0">
            <h2 className="text-base font-semibold">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

/** The orange "View all" link in a panel's corner. */
export function CardLink({ href, children }: { href: string; children: React.ReactNode }) {
  return <a href={href} className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-accent hover:underline">{children}<Icon name="arrow" className="h-3.5 w-3.5" /></a>;
}

const TONES = {
  neutral: "bg-white/[0.04] text-muted border-[#2a2a2a]",
  accent: "bg-accent/10 text-accent border-accent/30",
  good: "bg-good/10 text-good border-good/30",
  warn: "bg-warn/10 text-warn border-warn/30",
  bad: "bg-bad/10 text-bad border-bad/30",
} as const;

/** A small outlined tag, like "New Subscription" or "System" in a list row. */
export function Pill({ tone = "neutral", children, title }: { tone?: keyof typeof TONES; children: React.ReactNode; title?: string }) {
  return <span title={title} className={clsx("inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap", TONES[tone])}>{children}</span>;
}

/** A coloured dot and a word: Active, Past due. */
export function StatusDot({ tone, children }: { tone: "good" | "warn" | "bad" | "neutral"; children: React.ReactNode }) {
  const dot = { good: "bg-good", warn: "bg-accent", bad: "bg-bad", neutral: "bg-muted" }[tone];
  const text = { good: "text-good", warn: "text-accent", bad: "text-bad", neutral: "text-muted" }[tone];
  return <span className={clsx("inline-flex items-center gap-1.5 text-xs font-medium", text)}><span className={clsx("h-1.5 w-1.5 rounded-full", dot)} />{children}</span>;
}

/** Percentage change from the previous period; null when there is nothing to compare with. */
export function changePct(current: number, previous: number): number | null {
  if (!previous) return null;
  return Math.round(((current - previous) / previous) * 1000) / 10;
}

/** A KPI card: a one-line label with a small icon, the figure, and a footer with the explanation and how it moved against the previous 30 days. */
export function Stat({ label, value, hint, icon, trend }: { label: string; value: React.ReactNode; hint?: string; icon?: IconName; trend?: { current: number; previous: number } }) {
  const pct = trend ? changePct(trend.current, trend.previous) : null;
  const isNew = trend && !trend.previous && trend.current > 0;
  const flat = trend && !trend.previous && !trend.current;
  return (
    <div className="card flex flex-col rounded-xl border p-4 transition-colors hover:border-[#333] sm:p-5">
      <div className="flex items-center justify-between gap-3">
        <p className="truncate text-sm text-muted" title={label}>{label}</p>
        {icon && <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-accent/20 bg-accent/10 text-accent"><Icon name={icon} className="h-4 w-4" /></span>}
      </div>
      <p className="tnum mt-3 text-3xl font-semibold tracking-tight text-white">{value}</p>
      <div className="mt-2 flex items-start justify-between gap-2 text-xs">
        <span className="leading-snug text-muted">{hint ?? (trend ? "vs previous 30 days" : "")}</span>
        {trend && (
          <span title="Compared with the previous 30 days" className={clsx("tnum shrink-0 font-semibold", pct !== null && pct < 0 ? "text-bad" : isNew || (pct !== null && pct > 0) ? "text-accent" : "text-muted")}>
            {isNew ? "New" : flat || pct === 0 ? "+0.0%" : pct !== null ? `${pct > 0 ? "↑ " : "↓ "}${Math.abs(pct)}%` : ""}
          </span>
        )}
      </div>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("skeleton", className)} aria-hidden="true" />;
}

export function Empty({ children }: { children: React.ReactNode }) {
  if (children === "Loading...") {
    return (
      <div className="space-y-2.5 px-4 py-5" role="status" aria-label="Loading">
        <Skeleton className="h-4 w-2/3" /><Skeleton className="h-4 w-full" /><Skeleton className="h-4 w-5/6" />
      </div>
    );
  }
  return <p className="px-4 py-10 text-center text-sm text-muted">{children}</p>;
}

/** The same top of every page: the title and one sentence of help on the left, the page's own actions on the right. */
export function PageHeader({ title, description, actions }: { eyebrow?: string; title: string; description?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm leading-relaxed text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/** Initials on a tinted disc, so a list of buyers is scannable by shape and colour as well as by name. */
export function Avatar({ name, className }: { name: string; className?: string }) {
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("") || "?";
  const hue = [...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 0);
  return (
    <span aria-hidden="true" className={clsx("grid h-9 w-9 shrink-0 place-items-center rounded-full text-xs font-semibold text-white", className)} style={{ background: `hsl(${hue} 45% 38%)` }}>
      {initials}
    </span>
  );
}

export function ErrorNote({ children }: { children: React.ReactNode }) {
  return <p role="alert" className="rounded-md border border-bad/30 bg-bad/10 px-3 py-2 text-sm text-bad">{children}</p>;
}

export function Button({ variant = "default", className, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "primary" | "danger" | "ghost" | "glow" }) {
  const styles = {
    default: "border border-[#2a2a2a] bg-transparent text-white hover:border-[#444] hover:bg-white/[0.04]",
    glow: "border border-accent/40 bg-transparent text-accent hover:bg-accent/10",
    primary: "bg-accent font-semibold text-[#111] hover:brightness-110",
    danger: "border border-bad/40 bg-bad/10 text-bad hover:bg-bad/20",
    ghost: "hover:bg-white/[0.05]",
  } as const;
  return (
    <button
      {...props}
      className={clsx("rounded-lg px-3.5 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent", styles[variant], className)}
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
