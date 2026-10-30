import Link from "next/link";
import { fmtEtb } from "@/lib/format";
import { deliveryLabel, num, specsLine, type PortalUnit } from "@/lib/portal";
import Cover from "./Cover";

export default function ListingCard({ unit, href = `/portal/homes/${unit.id}` }: { unit: PortalUnit; href?: string }) {
  const delivery = deliveryLabel(unit.delivery_date);
  return (
    <Link href={href} className="group block overflow-hidden rounded-2xl border border-line bg-panel shadow-[0_1px_2px_rgb(60_40_10/0.05)] transition duration-300 hover:-translate-y-1 hover:shadow-[0_18px_40px_-18px_rgb(60_40_10/0.35)]">
      <div className="relative aspect-[4/3] overflow-hidden">
        <Cover unit={unit} className="h-full w-full transition duration-500 group-hover:scale-[1.04]" />
        <span className="absolute left-3 top-3 rounded-full bg-white/90 px-3 py-1 text-[11px] font-semibold uppercase tracking-wider text-ink backdrop-blur">{unit.developer_name}</span>
        {delivery && <span className="absolute bottom-3 left-3 rounded-full bg-ink/80 px-3 py-1 text-xs font-medium text-white backdrop-blur">{delivery}</span>}
      </div>
      <div className="p-5">
        <p className="display text-2xl font-medium tnum">{fmtEtb(num(unit.price_etb))}</p>
        <h3 className="mt-1 line-clamp-2 text-[15px] font-medium leading-snug">{unit.title}</h3>
        <p className="mt-2 text-sm text-muted">{specsLine(unit)}</p>
      </div>
    </Link>
  );
}
