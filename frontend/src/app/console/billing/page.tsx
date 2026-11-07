"use client";

import Link from "next/link";
import { Card, Empty, ErrorNote, PageHeader, Pill } from "@/components/ui";
import DemoBanner from "@/components/DemoBanner";
import { demoPrice, usagePercent, usageTone } from "@/lib/billing";
import { fmtDate, fmtEtb } from "@/lib/format";
import { useApi } from "@/lib/useApi";

interface Plan { plan: string; price_etb: number; fee_pct: number; current: boolean; can_upgrade: boolean; daily_messages: number; monthly_budget_usd: number; daily_jobs: number }
interface Invoice { id: string; number: string; plan: string; from_plan: string; amount_etb: number; created_at: string; demo: boolean }
interface Fee { id: string; plan: string; fee_pct: number; commission_etb: number; fee_etb: number; created_at: string; buyer_name: string | null; property_title: string | null }
interface Billing {
  plan: string; plans: Plan[]; invoices: Invoice[]; fees: Fee[]; fees_total_etb: number; invoices_total_etb: number;
  usage: { buyer_messages_today: number; daily_message_limit: number; jobs_today: number; daily_job_limit: number };
}

function Meter({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = usagePercent(used, limit);
  const tone = usageTone(pct);
  return (
    <div>
      <div className="flex justify-between text-sm"><span>{label}</span><span className="text-muted">{used.toLocaleString("en-US")} of {limit.toLocaleString("en-US")}</span></div>
      <div className="mt-1 h-2 rounded bg-line" role="progressbar" aria-label={label} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className={`h-2 rounded ${tone === "bad" ? "bg-bad" : tone === "warn" ? "bg-warn" : "bg-good"}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function BillingPage() {
  const billing = useApi<Billing>("billing");
  const b = billing.data;
  return (
    <div className="space-y-6">
      <PageHeader title="Billing" description="Your plan, what you have used against its limits, and what the platform keeps from your paid deals. Only owners can buy a plan; admins can look." />
      <DemoBanner />
      {billing.error && <ErrorNote>{billing.error}</ErrorNote>}
      {!b ? <Card><Empty>{billing.loading ? "Loading billing..." : "Billing is not available to your role."}</Empty></Card> : (
        <>
          <Card title="Plans" subtitle="Prices are placeholders for the demo. Upgrading opens a mock checkout: no card, no real payment.">
            <div className="grid gap-4 md:grid-cols-3">
              {b.plans.map((p) => (
                <div key={p.plan} className={`rounded-lg border p-4 ${p.current ? "border-accent" : "border-line"}`}>
                  <div className="flex items-center justify-between"><h3 className="font-semibold capitalize">{p.plan}</h3>{p.current && <Pill tone="good">Current plan</Pill>}</div>
                  <p className="mt-1 text-lg">{demoPrice(p.price_etb)}</p>
                  <ul className="mt-2 space-y-1 text-sm text-muted">
                    <li>{p.daily_messages.toLocaleString("en-US")} assistant messages a day</li>
                    <li>${p.monthly_budget_usd} monthly model budget</li>
                    <li>{p.daily_jobs.toLocaleString("en-US")} background jobs a day</li>
                    <li>Platform keeps {p.fee_pct}% of commission you are paid</li>
                  </ul>
                  {p.can_upgrade && <Link href={`/console/billing/checkout/${p.plan}`} className="mt-3 inline-block rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white">Upgrade to {p.plan}</Link>}
                </div>
              ))}
            </div>
          </Card>
          <Card title="Usage today" subtitle="Against the limits of your current plan.">
            <div className="space-y-4">
              <Meter label="Assistant messages" used={b.usage.buyer_messages_today} limit={b.usage.daily_message_limit} />
              <Meter label="Background jobs" used={b.usage.jobs_today} limit={b.usage.daily_job_limit} />
            </div>
          </Card>
          <Card title="Platform fee on your paid deals" subtitle={`A share of the commission when a deal is marked paid, at the percentage of your plan then. Demo: nothing is collected. Total ${fmtEtb(b.fees_total_etb)}.`}>
            {b.fees.length === 0 ? <Empty>No paid deals yet.</Empty> : (
              <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-muted"><tr><th className="pb-2 pr-4">Deal</th><th className="pb-2 pr-4">Commission</th><th className="pb-2 pr-4">Share</th><th className="pb-2 pr-4">Fee (demo)</th><th className="pb-2">Date</th></tr></thead>
                <tbody>{b.fees.map((f) => (
                  <tr key={f.id} className="border-t border-line"><td className="py-2 pr-4">{f.buyer_name ?? "Deal"}{f.property_title ? ` · ${f.property_title}` : ""}</td><td className="py-2 pr-4">{fmtEtb(f.commission_etb)}</td><td className="py-2 pr-4">{f.fee_pct}% ({f.plan})</td><td className="py-2 pr-4">{fmtEtb(f.fee_etb)}</td><td className="py-2 text-muted">{fmtDate(f.created_at)}</td></tr>
                ))}</tbody>
              </table></div>
            )}
          </Card>
          <Card title="Invoices" subtitle="One receipt for each plan you bought. Demo receipts: no real money was charged.">
            {b.invoices.length === 0 ? <Empty>No invoices yet.</Empty> : (
              <div className="overflow-x-auto"><table className="w-full min-w-[520px] text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-muted"><tr><th className="pb-2 pr-4">Number</th><th className="pb-2 pr-4">Plan</th><th className="pb-2 pr-4">Amount</th><th className="pb-2">Date</th></tr></thead>
                <tbody>{b.invoices.map((i) => (
                  <tr key={i.id} className="border-t border-line"><td className="py-2 pr-4">{i.number} <Pill>demo</Pill></td><td className="py-2 pr-4 capitalize">{i.from_plan} to {i.plan}</td><td className="py-2 pr-4">{fmtEtb(i.amount_etb)}</td><td className="py-2 text-muted">{fmtDate(i.created_at)}</td></tr>
                ))}</tbody>
              </table></div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
