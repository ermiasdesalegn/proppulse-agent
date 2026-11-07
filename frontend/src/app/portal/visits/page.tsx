import type { Metadata } from "next";
import Link from "next/link";
import Problem from "@/components/portal/Problem";
import VisitRow from "@/components/portal/VisitRow";
import { buttonClass } from "@/components/site/ui";
import { portalGet } from "@/lib/portal-server";
import { sortVisits, type Visit } from "@/lib/visits";

export const metadata: Metadata = { title: "My visits" };

export default async function VisitsPage() {
  const { status, data } = await portalGet<{ items: Visit[] }>("/visits");
  if (!data) return <Problem status={status} />;
  const items = sortVisits(data.items);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="display text-4xl font-medium">My visits</h1>
        <p className="mt-1 text-muted">{items.length === 0 ? "Visits you book with the concierge will be listed here." : "The visits you booked. You can cancel one before it starts."}</p>
      </div>
      {items.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-line bg-white/60 px-6 py-16 text-center">
          <p className="display text-2xl">No visits yet</p>
          <p className="mx-auto mt-2 max-w-md text-muted">Ask the concierge to book a site visit and it will show up here.</p>
          <Link href="/portal/chat" className={`${buttonClass("primary")} mt-6`}>Talk to the concierge</Link>
        </div>
      ) : (
        <ul className="space-y-3">{items.map((v) => <VisitRow key={v.id} visit={v} />)}</ul>
      )}
    </div>
  );
}
