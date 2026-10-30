import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import Layout3D from "@/components/portal/Layout3D";
import { SaveButton } from "@/components/portal/PortalControls";
import Problem from "@/components/portal/Problem";
import Cover from "@/components/site/Cover";
import { buttonClass } from "@/components/site/ui";
import { fmtEtb } from "@/lib/format";
import { deliveryLabel, describePlan, num, pricePerSqm, specsLine, type PortalUnitDetail } from "@/lib/portal";
import { portalGet } from "@/lib/portal-server";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function generateMetadata({ params }: { params: { id: string } }): Promise<Metadata> {
  if (!UUID.test(params.id)) return { title: "Home not found" };
  const { data } = await portalGet<PortalUnitDetail>(`/listings/${params.id}`);
  return { title: data?.title ?? "Home" };
}

function Fact({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <div className="min-w-0 rounded-xl bg-raised/70 px-4 py-3">
      <dt className="text-[11px] font-semibold uppercase tracking-wider text-muted">{label}</dt>
      <dd className="mt-0.5 line-clamp-3 break-words text-[15px] font-medium tnum" title={value}>{value}</dd>
    </div>
  );
}

export default async function HomePage({ params }: { params: { id: string } }) {
  if (!UUID.test(params.id)) notFound();
  const { status, data: unit } = await portalGet<PortalUnitDetail>(`/listings/${params.id}`);
  if (status === 404) notFound();
  if (!unit) return <Problem status={status} />;

  const perSqm = pricePerSqm(unit);
  const plan = describePlan(unit);
  const stage = unit.construction_stage_pct === null ? null : num(unit.construction_stage_pct);
  const yieldPct = num(unit.est_rental_yield_pct);
  const tags = (unit.vision_amenities?.tags ?? []).filter((t) => typeof t === "string" && t.trim()).slice(0, 16);
  const available = unit.status === "AVAILABLE";

  return (
    <div className="space-y-6">
      <Link href="/portal" className="inline-flex items-center gap-1.5 text-sm font-medium text-muted transition hover:text-ink">&larr; All homes</Link>

      <div className="grid gap-8 lg:grid-cols-[1.6fr_1fr]">
        <div className="space-y-8">
          <Cover unit={unit} className="aspect-[16/10] rounded-3xl" />

          {!available && <p role="status" className="rounded-xl border border-warn/30 bg-warn/10 px-4 py-3 text-sm font-medium text-warn">This home is no longer available. Our broker can suggest similar ones.</p>}

          <section aria-labelledby="about">
            <h2 id="about" className="display text-2xl font-medium">About this home</h2>
            <p className="mt-3 whitespace-pre-line leading-relaxed text-ink/85">{unit.description?.trim() || `${unit.unit_type} in ${unit.project_name}, ${unit.sub_city}, from ${unit.developer_name}. Ask our assistant or broker for the details that are not listed here.`}</p>
            {tags.length > 0 && (
              <ul className="mt-5 flex flex-wrap gap-2" aria-label="Features">
                {tags.map((t) => <li key={t} className="rounded-full border border-line bg-white px-3 py-1 text-sm capitalize">{t}</li>)}
              </ul>
            )}
          </section>

          {unit.has_layout && (
            <section aria-labelledby="layout">
              <h2 id="layout" className="display text-2xl font-medium">Floor layout in 3D</h2>
              <div className="mt-3"><Layout3D id={unit.id} title={unit.title} /></div>
            </section>
          )}

          {unit.floor_plan_image_url && /^https?:\/\//i.test(unit.floor_plan_image_url) && (
            <section aria-labelledby="plan">
              <h2 id="plan" className="display text-2xl font-medium">Floor plan</h2>
              <div className="mt-3 overflow-hidden rounded-2xl border border-line bg-white p-3">
                {/* eslint-disable-next-line @next/next/no-img-element -- hosted by the developer */}
                <img src={unit.floor_plan_image_url} alt={`Floor plan of ${unit.title}`} loading="lazy" referrerPolicy="no-referrer" className="mx-auto max-h-[32rem] w-auto" />
              </div>
            </section>
          )}
        </div>

        <aside className="lg:sticky lg:top-24 lg:self-start">
          <div className="rounded-3xl border border-line bg-white p-6 shadow-[0_24px_50px_-30px_rgb(60_40_10/0.4)]">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">{unit.developer_name}</p>
            <p className="display mt-2 text-4xl font-medium tnum">{fmtEtb(num(unit.price_etb))}</p>
            {perSqm && <p className="mt-1 text-sm text-muted tnum">{fmtEtb(perSqm)} per m²</p>}
            <h1 className="mt-4 text-lg font-medium leading-snug">{unit.title}</h1>
            <p className="mt-1 text-sm text-muted">{specsLine(unit)}</p>

            <dl className="mt-5 grid grid-cols-2 gap-2.5">
              <Fact label="Type" value={unit.unit_type} />
              <Fact label="Project" value={unit.project_name} />
              <Fact label="Bedrooms" value={unit.bedrooms === 0 ? "Studio" : String(unit.bedrooms)} />
              <Fact label="Size" value={num(unit.sqm) > 0 ? `${Math.round(num(unit.sqm))} m²` : null} />
              <Fact label="Delivery" value={deliveryLabel(unit.delivery_date)?.replace("Delivery ", "") ?? null} />
              <Fact label="Rental yield" value={yieldPct > 0 ? `${Number(yieldPct.toFixed(1))}% a year` : null} />
            </dl>

            {plan && (
              <div className="mt-5 rounded-xl border border-line px-4 py-3">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">Payment terms</p>
                <p className="mt-1 text-[15px]">{plan}</p>
              </div>
            )}

            {stage !== null && stage > 0 && (
              <div className="mt-5">
                <div className="flex items-center justify-between text-sm"><span className="text-muted">Construction</span><span className="font-medium tnum">{Math.round(stage)}%</span></div>
                <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-raised" role="progressbar" aria-valuenow={Math.round(stage)} aria-valuemin={0} aria-valuemax={100} aria-label="Construction progress">
                  <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(100, Math.round(stage))}%` }} />
                </div>
              </div>
            )}

            <div className="mt-6 flex flex-col gap-3">
              {available && <Link href={`/portal/chat?about=${unit.id}`} className={buttonClass("primary")}>Ask the assistant about this home</Link>}
              <SaveButton id={unit.id} saved={unit.saved} label />
              {unit.source_url && /^https?:\/\//i.test(unit.source_url) && (
                <a href={unit.source_url} target="_blank" rel="noopener noreferrer" className="text-center text-sm font-medium text-muted underline-offset-4 transition hover:text-ink hover:underline">View the original listing &#8599;</a>
              )}
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
