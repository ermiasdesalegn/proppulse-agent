import Link from "next/link";
import { fmtEtb } from "@/lib/format";
import { filtersToParams, hasFilters, SORTS, type Filters, type Listings } from "@/lib/portal";

const MIN_PRICES = [1_000_000, 5_000_000, 10_000_000, 20_000_000, 30_000_000];
const MAX_PRICES = [5_000_000, 10_000_000, 15_000_000, 20_000_000, 30_000_000, 50_000_000, 100_000_000];
const BEDROOMS = [1, 2, 3, 4, 5];

const control = "w-full rounded-xl border border-line bg-white px-3 py-2.5 text-sm outline-none";
const label = "mb-1 block text-xs font-semibold uppercase tracking-wider text-muted";

/** The filter bar is an ordinary form that sends its choices to the address, so it works without scripts and every search can be shared. */
export function FilterBar({ filters, facets, action }: { filters: Filters; facets: Listings["facets"]; action: string }) {
  return (
    <form action={action} method="get" role="search" aria-label="Filter homes" className="rounded-2xl border border-line bg-white p-4 shadow-[0_1px_2px_rgb(60_40_10/0.05)]">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
        <div className="sm:col-span-2 lg:col-span-2">
          <label htmlFor="f-q" className={label}>Search</label>
          <input id="f-q" name="q" defaultValue={filters.q} placeholder="Area, project or developer" autoComplete="off" className={control} />
        </div>
        <div>
          <label htmlFor="f-area" className={label}>Area</label>
          <select id="f-area" name="sub_city" defaultValue={filters.sub_city} className={control}>
            <option value="">All areas</option>
            {facets.sub_cities.map((a) => <option key={a.sub_city} value={a.sub_city}>{a.sub_city} ({a.units})</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="f-beds" className={label}>Bedrooms</label>
          <select id="f-beds" name="min_bedrooms" defaultValue={filters.min_bedrooms ?? ""} className={control}>
            <option value="">Any</option>
            {BEDROOMS.map((n) => <option key={n} value={n}>{n}+</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="f-min" className={label}>Min price</label>
          <select id="f-min" name="price_min" defaultValue={filters.price_min ?? ""} className={control}>
            <option value="">No minimum</option>
            {MIN_PRICES.map((p) => <option key={p} value={p}>{fmtEtb(p)}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="f-max" className={label}>Max price</label>
          <select id="f-max" name="price_max" defaultValue={filters.price_max ?? ""} className={control}>
            <option value="">No maximum</option>
            {MAX_PRICES.map((p) => <option key={p} value={p}>{fmtEtb(p)}</option>)}
          </select>
        </div>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <div>
          <label htmlFor="f-dev" className={label}>Developer</label>
          <select id="f-dev" name="developer" defaultValue={filters.developer} className={control}>
            <option value="">All developers</option>
            {facets.developers.map((d) => <option key={d.slug} value={d.slug}>{d.name} ({d.units})</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="f-sort" className={label}>Sort by</label>
          <select id="f-sort" name="sort" defaultValue={filters.sort} className={control}>
            {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </div>
        <div className="flex gap-2">
          <button type="submit" className="flex-1 rounded-full bg-accent px-6 py-2.5 text-sm font-semibold text-white transition hover:brightness-110 sm:flex-none">Show homes</button>
          {hasFilters(filters) && <Link href={action} className="rounded-full border border-ink/20 px-4 py-2.5 text-sm font-semibold transition hover:border-ink/50">Clear</Link>}
        </div>
      </div>
    </form>
  );
}

/** What is filtered right now, each with a link that takes just that one away. */
export function ActiveFilters({ filters, action, developers }: { filters: Filters; action: string; developers: Listings["facets"]["developers"] }) {
  const chips: { text: string; without: Partial<Filters> }[] = [];
  if (filters.q) chips.push({ text: `"${filters.q}"`, without: { q: "" } });
  if (filters.sub_city) chips.push({ text: filters.sub_city, without: { sub_city: "" } });
  if (filters.developer) chips.push({ text: developers.find((d) => d.slug === filters.developer)?.name ?? filters.developer, without: { developer: "" } });
  if (filters.min_bedrooms !== null) chips.push({ text: `${filters.min_bedrooms}+ bedrooms`, without: { min_bedrooms: null } });
  if (filters.price_min !== null) chips.push({ text: `from ${fmtEtb(filters.price_min)}`, without: { price_min: null } });
  if (filters.price_max !== null) chips.push({ text: `up to ${fmtEtb(filters.price_max)}`, without: { price_max: null } });
  if (chips.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Active filters">
      {chips.map((c) => {
        const query = filtersToParams(filters, { ...c.without, page: 1 }).toString();
        return (
          <li key={c.text}>
            <Link href={query ? `${action}?${query}` : action} aria-label={`Remove filter: ${c.text}`} className="inline-flex items-center gap-1.5 rounded-full border border-line bg-white px-3 py-1 text-sm transition hover:border-accent">
              {c.text}<span aria-hidden="true" className="text-muted">×</span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
