"use client";

import { useState } from "react";
import clsx from "@/components/clsx";
import { Columns, HBars } from "@/components/charts";
import { PageHeader, Card, Empty, ErrorNote, Pill, Stat } from "@/components/ui";
import { STAGE_LABEL, fallbackRate, messageSeries, scoreBandLabel, scoreBands } from "@/lib/analytics";
import { fmtDuration, fmtEtb, fmtPct } from "@/lib/format";
import type { Analytics } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const WINDOWS = [7, 30, 90];

export default function AnalyticsPage() {
  const [days, setDays] = useState(30);
  const data = useApi<Analytics>(`analytics?days=${days}`, 60_000);
  const a = data.data;

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Insight" title="Analytics" description={<>How the pipeline, the conversations and the AI are doing. Everything here is counted from the database.</>} actions={<><div role="group" aria-label="Time window" className="inline-flex rounded-lg border border-line bg-raised p-0.5">
          {WINDOWS.map((w) => (
            <button key={w} onClick={() => setDays(w)} aria-pressed={days === w}
              className={clsx("rounded-md px-3 py-1 text-xs font-medium transition-colors", days === w ? "bg-accent text-[#0a0a1f]" : "text-muted hover:text-white")}>Last {w} days</button>
          ))}
        </div></>} />
      {data.error && <ErrorNote>{data.error}</ErrorNote>}
      {data.loading && !a ? <Empty>Counting...</Empty> : a && <Body a={a} />}
    </div>
  );
}

function Body({ a }: { a: Analytics }) {
  const openDeals = a.ledger.filter((l) => !["COMMISSION_PAID", "CLOSED_LOST"].includes(l.status)).reduce((n, l) => n + l.deals, 0);
  const messages = messageSeries(a);
  const tokensPerMessage = a.totals.messages ? Math.round(a.models.reduce((n, m) => n + m.input_tokens + m.output_tokens, 0) / Math.max(a.totals.messages, 1)) : 0;

  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Stat label="Buyers" value={a.totals.buyers} hint={`${a.totals.messages.toLocaleString("en-US")} messages in ${a.days} days`} />
        <Stat label="Tours and calls booked" value={a.totals.tours} hint={`last ${a.days} days`} />
        <Stat label="Commission in the pipeline" value={fmtEtb(a.commission.open_etb)} hint={`${openDeals} open deal${openDeals === 1 ? "" : "s"}`} />
        <Stat label="Commission paid" value={fmtEtb(a.commission.paid_etb)} />
        <Stat label="Typical reply time" value={a.turns.p50_ms !== null ? fmtDuration(a.turns.p50_ms) : "-"} hint={a.turns.p95_ms !== null ? `slowest 1 in 20: ${fmtDuration(a.turns.p95_ms)}` : "no replies yet"} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Where buyers are in the pipeline">
          <HBars title="Buyers per stage" rows={a.funnel.map((f) => ({ label: STAGE_LABEL[f.stage] ?? f.stage, value: f.buyers }))} />
        </Card>
        <Card title="Commission by deal stage" action={<span className="text-xs text-muted">expected, in birr</span>}>
          <HBars title="Expected commission per deal stage" format={(v) => fmtEtb(v)} empty="No deals yet. A booked tour registers one deal per developer."
            rows={a.ledger.map((l) => ({ label: STAGE_LABEL[l.status] ?? l.status, value: l.expected_commission_etb, note: `${l.deals} deal${l.deals === 1 ? "" : "s"}` }))} />
        </Card>
        <Card title="Messages per day" action={<span className="text-xs text-muted">buyers and agent together</span>}>
          <Columns title="Messages per day" points={messages} emphasizeLast />
        </Card>
        <Card title="How qualified the buyers are" action={<span className="text-xs text-muted">lead score bands</span>}>
          <Columns title="Buyers per lead score band" points={scoreBands(a.score_distribution).map((b) => ({ label: scoreBandLabel(b), value: b.buyers }))} />
        </Card>
        <Card title="What buyers worry about">
          <HBars title="Most common worries" empty="No worries raised yet. The agent notes them when a buyer says price, delivery or trust is a concern."
            rows={a.objections.map((o) => ({ label: o.text, value: o.n }))} />
        </Card>
        <Card title="Where the time goes" action={<span className="text-xs text-muted">average model request per step</span>}>
          <HBars title="Average model time per step" format={(v) => fmtDuration(v)} empty="No model requests in this period."
            rows={a.nodes.map((n) => ({ label: n.node, value: n.avg_ms, note: `${n.calls} request${n.calls === 1 ? "" : "s"}` }))} />
        </Card>
      </div>

      <Card title="The AI behind it" action={<Pill tone={fallbackRate(a.models) > 25 ? "warn" : "good"}>{fallbackRate(a.models)}% fell back to another model</Pill>}>
        {a.models.length === 0 ? <Empty>No model requests in this period.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] text-left text-sm">
              <thead className="border-b border-line text-xs text-muted"><tr><th className="px-4 py-2 font-medium">Model</th><th className="px-2 py-2 text-right font-medium">Requests</th><th className="px-2 py-2 text-right font-medium">Average time</th><th className="px-2 py-2 text-right font-medium">Tokens</th><th className="px-2 py-2 text-right font-medium">Fell back</th><th className="px-4 py-2 text-right font-medium">Failed</th></tr></thead>
              <tbody>
                {a.models.map((m) => (
                  <tr key={m.model} className="border-b border-line/60">
                    <td className="px-4 py-2 font-mono text-xs">{m.model}</td>
                    <td className="tnum px-2 py-2 text-right">{m.calls}</td>
                    <td className="tnum px-2 py-2 text-right">{m.avg_ms ? fmtDuration(m.avg_ms) : "-"}</td>
                    <td className="tnum px-2 py-2 text-right">{(m.input_tokens + m.output_tokens).toLocaleString("en-US")}</td>
                    <td className={clsx("tnum px-2 py-2 text-right", m.fell_back > 0 && "text-warn")}>{m.fell_back}</td>
                    <td className={clsx("tnum px-4 py-2 text-right", m.errors > 0 && "text-bad")}>{m.errors}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="border-t border-line px-4 py-3 text-xs text-muted">
          A conversation costs about {a.per_conversation.model_calls} model requests and {a.per_conversation.tokens.toLocaleString("en-US")} tokens on average
          {tokensPerMessage ? ` (about ${tokensPerMessage.toLocaleString("en-US")} tokens per message)` : ""}. On the free tier that costs nothing in money, and the daily request limit is the real ceiling.
        </p>
      </Card>

      <Card title="Quality checks" action={a.evaluation && <span className="text-xs text-muted">last run {new Date(a.evaluation.ran_at).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}</span>}>
        {!a.evaluation ? <Empty>No evaluation results yet. Run `python -m evals` in the backend folder.</Empty> : (
          <div className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-4">
            {a.evaluation.retrieval && <>
              <Stat label="Finds a right home in its top 3" value={fmtPct(a.evaluation.retrieval.hit_at_3 * 100)} hint={`${a.evaluation.retrieval.cases} test messages, English, Amharic and mixed`} />
              <Stat label="Right home ranked first (MRR)" value={a.evaluation.retrieval.mrr.toFixed(2)} hint="1.00 is always first" />
            </>}
            {a.evaluation.scenarios && <>
              <Stat label="Conversations that go to plan" value={`${a.evaluation.scenarios.passed}/${a.evaluation.scenarios.counted}`} hint={`${a.evaluation.scenarios.known_gaps} known gap${a.evaluation.scenarios.known_gaps === 1 ? "" : "s"} listed`} />
              <Stat label="Replies with only known numbers" value={`${a.evaluation.scenarios.grounded_turns}/${a.evaluation.scenarios.turns}`} hint="no invented prices" />
            </>}
          </div>
        )}
      </Card>
    </>
  );
}
