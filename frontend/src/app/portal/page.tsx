import type { Metadata } from "next";
import Link from "next/link";
import { ActiveFilters, FilterBar } from "@/components/portal/Filters";
import PortalCard from "@/components/portal/PortalCard";
import Problem from "@/components/portal/Problem";
import { buttonClass } from "@/components/site/ui";
import { apiQuery, filtersToParams, PAGE_SIZE, parseFilters, type Listings } from "@/lib/portal";
import { portalGet } from "@/lib/portal-server";

export const metadata: Metadata = { title: "Browse homes" };

export default async function BrowsePage({ searchParams }: { searchParams: Record<string, string | string[] | undefined> }) {
  const filters = parseFilters(searchParams);
  const { status, data } = await portalGet<Listings>(`/listings?${apiQuery(filters)}`);
  if (!data) return <Problem status={status} />;

  const saved = new Set(data.saved);
  const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
  const link = (page: number) => {
    const query = filtersToParams(filters, { page }).toString();
    return query ? `/portal?${query}` : "/portal";
  };
  const from = data.total === 0 ? 0 : (filters.page - 1) * PAGE_SIZE + 1;
  const to = Math.min(data.total, filters.page * PAGE_SIZE);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="display text-4xl font-medium">Find your home</h1>
          <p className="mt-1 text-muted" aria-live="polite">{data.total === 0 ? "No homes match yet" : `${data.total.toLocaleString("en-US")} ${data.total === 1 ? "home" : "homes"}${filters.q || filters.sub_city ? " found" : " available"}`}</p>
        </div>
        <Link href={`/portal/map${filtersToParams(filters, { page: 1 }).toString() ? `?${filtersToParams(filters, { page: 1 })}` : ""}`} className={buttonClass("outline")}>View on map</Link>
      </div>

      <FilterBar filters={filters} facets={data.facets} action="/portal" />
      <ActiveFilters filters={filters} action="/portal" developers={data.facets.developers} />

      {data.items.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-line bg-white/60 px-6 py-16 text-center">
          <p className="display text-2xl">Nothing matches those filters</p>
          <p className="mt-2 text-muted">Try a wider price range, fewer bedrooms or a different area.</p>
          <Link href="/portal" className={`${buttonClass("primary")} mt-6`}>Clear all filters</Link>
        </div>
      ) : (
        <>
          <p className="text-sm text-muted tnum">Showing {from}–{to} of {data.total.toLocaleString("en-US")}</p>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {data.items.map((u) => <PortalCard key={u.id} unit={u} saved={saved.has(u.id)} />)}
          </div>
        </>
      )}

      {pages > 1 && (
        <nav aria-label="Pages" className="flex items-center justify-center gap-3 pt-4">
          {filters.page > 1 ? <Link href={link(filters.page - 1)} rel="prev" className={buttonClass("outline")}>Previous</Link> : <span className={`${buttonClass("outline")} pointer-events-none opacity-40`}>Previous</span>}
          <span className="text-sm text-muted tnum">Page {filters.page} of {pages}</span>
          {filters.page < pages ? <Link href={link(filters.page + 1)} rel="next" className={buttonClass("outline")}>Next</Link> : <span className={`${buttonClass("outline")} pointer-events-none opacity-40`}>Next</span>}
        </nav>
      )}
    </div>
  );
}
