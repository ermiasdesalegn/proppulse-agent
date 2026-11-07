"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { Button, Card, Empty, ErrorNote, PageHeader } from "@/components/ui";
import { DEMO_BANNER, demoPrice, isUpgrade, purchasablePlan } from "@/lib/billing";
import { errorMessage, post } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import DemoBanner from "@/components/DemoBanner";

interface Billing { plan: string; plans: { plan: string; price_etb: number }[] }

/** The mock checkout. "Pay (demo)" buys the plan at once; "Cancel" only goes back, so nothing changes. */
export default function CheckoutPage() {
  const params = useParams<{ plan: string }>();
  const plan = purchasablePlan(params.plan);
  const router = useRouter();
  const billing = useApi<Billing>(plan ? "billing" : null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const price = billing.data?.plans.find((p) => p.plan === plan)?.price_etb;
  const ok = plan !== null && billing.data !== null && billing.data !== undefined && isUpgrade(billing.data.plan, plan);

  async function pay() {
    if (!plan) return;
    setBusy(true);
    setError(null);
    try { await post("billing/upgrade", { plan }); router.push("/console/billing"); } catch (e) { setError(errorMessage(e)); setBusy(false); }
  }

  return (
    <div className="mx-auto max-w-lg space-y-6">
      <PageHeader title="Demo checkout" description="A stand-in for a payment page. There is no card form because nothing is charged." />
      <DemoBanner />
      {billing.error && <ErrorNote>{billing.error}</ErrorNote>}
      <Card>
        {!plan ? <Empty>That plan cannot be bought.</Empty>
          : !billing.data ? <Empty>Loading...</Empty>
          : !ok ? <Empty>Your agency is already on this plan or a higher one.</Empty>
          : (
            <div className="space-y-4">
              <div><p className="text-xs uppercase tracking-wide text-muted">You are buying</p><p className="text-xl font-semibold capitalize">{plan} plan</p></div>
              <div><p className="text-xs uppercase tracking-wide text-muted">Price</p><p className="text-lg">{price === undefined ? "-" : demoPrice(price)}</p></div>
              <p className="text-sm text-muted">{DEMO_BANNER}. The plan and its limits change as soon as you pay.</p>
              {error && <p role="alert" className="text-sm text-bad">{error}</p>}
              <div className="flex gap-2">
                <Button variant="primary" disabled={busy} onClick={() => void pay()}>{busy ? "Paying..." : "Pay (demo)"}</Button>
                <Link href="/console/billing" className="rounded-md border border-line px-3 py-1.5 text-sm">Cancel</Link>
              </div>
            </div>
          )}
        {(!plan || !ok) && billing.data !== undefined && <Link href="/console/billing" className="mt-3 inline-block text-sm text-accent">Back to billing</Link>}
      </Card>
    </div>
  );
}
