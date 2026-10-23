const TZ = "Africa/Addis_Ababa";

export function fmtEtb(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  if (Math.abs(value) >= 1_000_000) return `${Number((value / 1_000_000).toFixed(2))}M ETB`;
  return `${Math.round(value).toLocaleString("en-US")} ETB`;
}

export function fmtPct(value: number): string {
  return `${Number(value.toFixed(1))}%`;
}

export function fmtDateTime(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", { timeZone: TZ, weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso));
}

export function fmtTime(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date(iso));
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  return new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", day: "2-digit", month: "short", year: "numeric" }).format(new Date(iso));
}

export function timeAgo(iso: string, now = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

export function fmtDuration(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function displayName(buyer: { full_name: string | null; thread_id: string }): string {
  if (buyer.full_name) return buyer.full_name;
  const [channel, id] = buyer.thread_id.split(":");
  return channel === "tg" ? `Telegram ${id}` : channel === "web" ? `Web visitor ${id.slice(0, 6)}` : buyer.thread_id;
}

export function stageLabel(stage: string): string {
  return stage.toLowerCase().split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
}

export function kmFromMeters(meters: number): string {
  return `${(meters / 1000).toFixed(1)} km`;
}
