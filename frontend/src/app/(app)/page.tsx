"use client";

import { useEffect } from "react";
import { ActivityList, useBuyerNames } from "@/components/Activity";
import Link from "next/link";
import { Icon, type IconName } from "@/components/icons";
import { Card, CardLink, Empty, ErrorNote, PageHeader, Pill, Stat } from "@/components/ui";
import { fmtEtb, timeAgo } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { ProactiveEvent, Stats } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const KIND_LABEL: Record<string, string> = {
  PRICE_DROP: "Price drop", PRICE_INCREASE: "Price increase", NEW_LISTING: "New listing", TOUR_REMINDER: "Tour reminder", MILESTONE_REMINDER: "Payment reminder",
  MILESTONE_OVERDUE: "Overdue payment", COMMISSION_READY: "Commission ready", TAKEOVER_MATCH: "Match in a chat you hold",
};

const KIND_ICON: Record<string, IconName> = {
  PRICE_DROP: "deals", PRICE_INCREASE: "deals", NEW_LISTING: "listing", TOUR_REMINDER: "calendar", MILESTONE_REMINDER: "clock", MILESTONE_OVERDUE: "clock", COMMISSION_READY: "money", TAKEOVER_MATCH: "chat",
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
        title="Overview"
        description={s ? <>{s.buyers} buyers and <span className="tnum text-white">{fmtEtb(Number(s.pipeline_commission_etb))}</span> in the commission pipeline, {s.upcoming_tours} upcoming {s.upcoming_tours === 1 ? "tour" : "tours"}.</> : "Your buyers, deals and the agent at a glance."}
        actions={<><Link href="/board" className="rounded-lg border border-[#2a2a2a] px-3.5 py-1.5 text-sm font-medium hover:border-[#444] hover:bg-white/[0.04]">View deal board</Link><Link href="/chat" className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3.5 py-1.5 text-sm font-semibold text-[#111] hover:brightness-110"><Icon name="plus" className="h-4 w-4" />New conversation</Link></>}
      />

      {stats.error && <ErrorNote>{stats.error}</ErrorNote>}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
        <Stat label="Buyers" icon="buyers" value={s?.buyers ?? "-"} trend={s?.trends?.buyers} hint={s ? `${s.qualified} qualified, ${s.in_takeover} in takeover` : undefined} />
        <Stat label="Open deals" icon="deals" value={s?.open_deals ?? "-"} trend={s?.trends?.open_deals} hint="Registered, toured, signed or pending" />
        <Stat label="Pipeline commission" icon="money" value={s ? fmtEtb(Number(s.pipeline_commission_etb)) : "-"} trend={s?.trends?.pipeline_commission_etb} hint="Expected, across open deals" />
        <Stat label="Commission pending" icon="clock" value={s ? fmtEtb(Number(s.commission_pending_etb)) : "-"} hint="Developers are releasing it" />
        <Stat label="Upcoming tours" icon="calendar" value={s?.upcoming_tours ?? "-"} trend={s?.trends?.upcoming_tours} />
        <Stat label="Proactive messages, 24 h" icon="bell" value={s?.proactive_sent ?? "-"} trend={s?.trends?.proactive_sent} hint="Alerts and reminders sent" />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Live activity" subtitle="Every step the agent takes, as it happens" action={<Pill tone="accent">streaming</Pill>}>
          <ActivityList events={events} nameOf={nameOf} />
        </Card>

        <Card title="Recent activity" subtitle="Alerts and reminders that went out" action={<CardLink href="/analytics">View analytics</CardLink>}>
          {notices.loading ? <Empty>Loading...</Empty> : (notices.data ?? []).length === 0 ? (
            <Empty>Nothing yet. Price drops, tour reminders and payment notices appear here when they are sent.</Empty>
          ) : (
            <ul className="max-h-[28rem] overflow-y-auto">
              {notices.data!.map((n) => (
                <li key={n.id} className="flex items-start gap-3 border-t border-[#222] px-5 py-3.5 text-sm">
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-accent/10 text-accent"><Icon name={KIND_ICON[n.kind] ?? "bell"} className="h-4 w-4" /></span>
                  <div className="min-w-0 flex-1">
                    <p className="break-words font-medium text-white">{n.message}</p>
                    <p className="mt-0.5 text-xs text-muted">{n.audience === "broker" ? "to you" : `to ${n.buyer_name ?? "a buyer"}`} via {n.channel} - {timeAgo(n.created_at)}</p>
                  </div>
                  <Pill tone={n.audience === "broker" ? "warn" : "good"}>{KIND_LABEL[n.kind] ?? n.kind}</Pill>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
