"use client";

import { useEffect } from "react";
import { ActivityList, useBuyerNames } from "@/components/Activity";
import Link from "next/link";
import { Card, Empty, ErrorNote, PageHeader, Pill, Stat } from "@/components/ui";
import { fmtEtb, timeAgo } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { ProactiveEvent, Stats } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const KIND_LABEL: Record<string, string> = {
  PRICE_DROP: "Price drop", NEW_LISTING: "New listing", TOUR_REMINDER: "Tour reminder", MILESTONE_REMINDER: "Payment reminder",
  MILESTONE_OVERDUE: "Overdue payment", COMMISSION_READY: "Commission ready", TAKEOVER_MATCH: "Match in a chat you hold",
};

export default function OverviewPage() {
  const { events, dataVersion } = useLive();
  const nameOf = useBuyerNames();
  const stats = useApi<Stats>("stats", 30_000);
  const notices = useApi<ProactiveEvent[]>("proactive/events?limit=15", 60_000);

  useEffect(() => {
    if (dataVersion === 0) return;
    void stats.reload();
    void notices.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataVersion]);

  const s = stats.data;
  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Overview" title="Good to see you"
        description={s ? <>{s.buyers} buyers and <span className="tnum text-white">{fmtEtb(Number(s.pipeline_commission_etb))}</span> in the commission pipeline, {s.upcoming_tours} upcoming {s.upcoming_tours === 1 ? "tour" : "tours"}.</> : "Your buyers, deals and the agent at a glance."}
        actions={<><Link href="/chat" className="rounded-lg border border-[#2e2e52] px-3.5 py-1.5 text-sm font-medium hover:border-[#4a4a74]">Open chat simulator</Link><Link href="/board" className="rounded-lg bg-accent px-3.5 py-1.5 text-sm font-semibold text-[#0a0a1f] hover:brightness-110">View deal board</Link></>}
      />

      {stats.error && <ErrorNote>{stats.error}</ErrorNote>}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 2xl:grid-cols-6">
        <Stat label="Buyers" icon="buyers" value={s?.buyers ?? "-"} hint={s ? `${s.qualified} qualified, ${s.in_takeover} in takeover` : undefined} />
        <Stat label="Open deals" icon="deals" value={s?.open_deals ?? "-"} hint="Registered, toured, signed or pending" />
        <Stat label="Pipeline commission" icon="money" value={s ? fmtEtb(Number(s.pipeline_commission_etb)) : "-"} hint="Expected, across open deals" />
        <Stat label="Commission pending" icon="clock" value={s ? fmtEtb(Number(s.commission_pending_etb)) : "-"} hint="Developers are releasing it" />
        <Stat label="Upcoming tours" icon="calendar" value={s?.upcoming_tours ?? "-"} />
        <Stat label="Proactive messages, 24 h" icon="bell" value={s?.proactive_sent ?? "-"} hint="Alerts and reminders sent" />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Live activity" action={<Pill tone="accent">streaming</Pill>}>
          <ActivityList events={events} nameOf={nameOf} />
        </Card>

        <Card title="Notices and proactive messages">
          {notices.loading ? <Empty>Loading...</Empty> : (notices.data ?? []).length === 0 ? (
            <Empty>Nothing yet. Price drops, tour reminders and payment notices appear here when they are sent.</Empty>
          ) : (
            <ul className="max-h-[28rem] overflow-y-auto">
              {notices.data!.map((n) => (
                <li key={n.id} className="border-b border-line/60 px-4 py-3 text-sm last:border-b-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Pill tone={n.audience === "broker" ? "warn" : "good"}>{KIND_LABEL[n.kind] ?? n.kind}</Pill>
                    <span className="text-xs text-muted">{n.audience === "broker" ? "to you" : `to ${n.buyer_name ?? "a buyer"}`} via {n.channel} - {timeAgo(n.created_at)}</span>
                  </div>
                  <p className="mt-1 break-words text-muted">{n.message}</p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
