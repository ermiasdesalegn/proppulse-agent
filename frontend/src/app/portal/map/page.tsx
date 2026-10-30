import type { Metadata } from "next";
import Link from "next/link";
import { ActiveFilters, FilterBar } from "@/components/portal/Filters";
import PortalMap from "@/components/portal/PortalMap";
import Problem from "@/components/portal/Problem";
import { buttonClass } from "@/components/site/ui";
import { apiQuery, filtersToParams, parseFilters, type Listings } from "@/lib/portal";
import { portalGet } from "@/lib/portal-server";

export const metadata: Metadata = { title: "Homes on the map" };

const MAP_LIMIT = 60;

export default async function MapPage({ searchParams }: { searchParams: Record<string, string | string[] | undefined> }) {
  const filters = { ...parseFilters(searchParams), page: 1 };
  const { status, data } = await portalGet<Listings>(`/listings?${apiQuery(filters, MAP_LIMIT)}`);
  if (!data) return <Problem status={status} />;
  const query = filtersToParams(filters).toString();

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="display text-4xl font-medium">Homes on the map</h1>
          <p className="mt-1 text-muted">See where each home is, and what is nearby.</p>
        </div>
        <Link href={query ? `/portal?${query}` : "/portal"} className={buttonClass("outline")}>View as a list</Link>
      </div>
      <FilterBar filters={filters} facets={data.facets} action="/portal/map" />
      <ActiveFilters filters={filters} action="/portal/map" developers={data.facets.developers} />
      <PortalMap key={query} units={data.items} total={data.total} />
    </div>
  );
}
