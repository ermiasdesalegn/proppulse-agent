import { DEMO_BANNER } from "@/lib/billing";

/** On every screen that shows a price, a payment, an invoice or a platform fee. */
export default function DemoBanner() {
  return <div role="note" className="rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-sm font-semibold text-warn">{DEMO_BANNER}</div>;
}
