import type { Metadata } from "next";
import Link from "next/link";
import PortalCard from "@/components/portal/PortalCard";
import Problem from "@/components/portal/Problem";
import { buttonClass } from "@/components/site/ui";
import type { PortalUnit } from "@/lib/portal";
import { portalGet } from "@/lib/portal-server";

export const metadata: Metadata = { title: "Saved homes" };

export default async function SavedPage() {
  const { status, data } = await portalGet<{ items: PortalUnit[] }>("/saved");
  if (!data) return <Problem status={status} />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="display text-4xl font-medium">Saved homes</h1>
        <p className="mt-1 text-muted">{data.items.length === 0 ? "Homes you save will wait for you here." : `${data.items.length} saved, newest first`}</p>
      </div>
      {data.items.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-line bg-white/60 px-6 py-16 text-center">
          <p className="display text-2xl">Nothing saved yet</p>
          <p className="mx-auto mt-2 max-w-md text-muted">Tap the heart on any home to keep it here, so you can compare and come back to it.</p>
          <Link href="/portal" className={`${buttonClass("primary")} mt-6`}>Browse homes</Link>
        </div>
      ) : (
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {data.items.map((u) => <PortalCard key={u.id} unit={u} saved />)}
        </div>
      )}
    </div>
  );
}
