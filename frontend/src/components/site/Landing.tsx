import Link from "next/link";
import { fmtEtb } from "@/lib/format";
import { num, specsLine, type Featured, type PortalUnit } from "@/lib/portal";
import HeroPhoto from "./HeroPhoto";
import ListingCard from "./ListingCard";
import { buttonClass } from "./ui";

const wrap = "mx-auto max-w-7xl px-4 sm:px-6 lg:px-8";

function Eyebrow({ children }: { children: React.ReactNode }) {
  return <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">{children}</p>;
}

/* ---------------- hero ---------------- */

/** The building drawing, shown only when the hero home has no photo or its photo cannot load. */
function HeroDrawing() {
  return (
    <svg viewBox="0 0 400 500" preserveAspectRatio="xMidYMax slice" className="absolute inset-0 h-full w-full" aria-hidden="true">
      <circle cx="300" cy="120" r="46" fill="#fff" opacity=".4" />
      <circle cx="300" cy="120" r="80" fill="#fff" opacity=".12" />
      <path d="M0 430h400v70H0z" fill="#6d4f1f" opacity=".18" />
      <g fill="none" stroke="#5c4119" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" opacity=".7">
        <rect x="70" y="170" width="130" height="260" rx="3" />
        <rect x="200" y="110" width="120" height="320" rx="3" />
        <rect x="320" y="230" width="60" height="200" rx="3" />
        {[0, 1, 2, 3, 4, 5, 6].map((r) => [0, 1, 2].map((c) => <rect key={`a${r}${c}`} x={84 + c * 38} y={186 + r * 34} width="24" height="16" rx="1.5" />))}
        {[0, 1, 2, 3, 4, 5, 6, 7, 8].map((r) => [0, 1, 2].map((c) => <rect key={`b${r}${c}`} x={214 + c * 34} y={126 + r * 34} width="22" height="16" rx="1.5" />))}
        {[0, 1, 2, 3, 4].map((r) => <rect key={`c${r}`} x="334" y={246 + r * 34} width="32" height="16" rx="1.5" />)}
        <path d="M20 430h360" />
        <path d="M34 430v-70M34 360c-22-8-30-32-18-50 8 14 28 16 36 50-6 6-14 6-18 0Z" opacity=".6" />
      </g>
    </svg>
  );
}

function HeroArt({ unit }: { unit?: PortalUnit }) {
  return (
    <div className="relative mx-auto w-full max-w-xl lg:max-w-none">
      <div className="relative aspect-[4/5] overflow-hidden rounded-[2rem] bg-gradient-to-br from-[#f3e7d3] via-[#e4cda4] to-[#b88f4e] shadow-[0_40px_80px_-30px_rgb(90_60_20/0.5)] sm:aspect-[5/5] lg:aspect-[4/5]">
        <HeroPhoto unit={unit} fallback={<HeroDrawing />} />
      </div>

      {unit && (
        <Link href={`/portal/homes/${unit.id}`} className="absolute -left-2 bottom-8 w-60 rounded-2xl border border-line bg-white p-4 shadow-xl transition hover:-translate-y-0.5 sm:-left-8">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-accent">Just listed</p>
          <p className="display mt-1 text-xl font-medium tnum">{fmtEtb(num(unit.price_etb))}</p>
          <p className="mt-0.5 line-clamp-1 text-sm">{unit.title}</p>
          <p className="mt-1 text-xs text-muted">{specsLine(unit)}</p>
        </Link>
      )}

      <div className="absolute -right-2 top-10 w-64 rounded-2xl border border-line bg-white p-4 shadow-xl sm:-right-6" aria-label="Example conversation">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">Example conversation</p>
        <p className="mt-2 ml-auto w-fit max-w-[90%] rounded-2xl rounded-br-md bg-ink px-3 py-2 text-[13px] text-white">3 bedrooms in Bole, around 25 million?</p>
        <p className="mt-2 w-fit max-w-[92%] rounded-2xl rounded-bl-md bg-raised px-3 py-2 text-[13px]">Here are homes that fit. Want me to compare the first two, or book a visit?</p>
      </div>
    </div>
  );
}

export function Hero({ unit }: { unit?: PortalUnit }) {
  return (
    <section className={`${wrap} grid items-center gap-14 pb-20 pt-12 lg:grid-cols-[1.05fr_0.95fr] lg:pb-28 lg:pt-20`}>
      <div className="rise">
        <Eyebrow>Addis Ababa · Homes, plainly explained</Eyebrow>
        <h1 className="display mt-5 text-[2.6rem] font-medium leading-[1.05] sm:text-6xl lg:text-[4.4rem]">
          Find a home in Addis Ababa, <span className="italic text-accent">with someone who answers.</span>
        </h1>
        <p className="mt-6 max-w-xl text-lg leading-relaxed text-muted">
          Browse listings from the city&apos;s developers and property portals in one place. Ask our assistant anything, in English or Amharic, and a real broker is there when you are ready to visit.
        </p>

        <form action="/portal" method="get" role="search" className="mt-9 flex max-w-xl flex-col gap-2 rounded-3xl border border-line bg-white p-2 shadow-[0_20px_40px_-24px_rgb(60_40_10/0.35)] sm:flex-row sm:items-center sm:rounded-full">
          <label htmlFor="hero-q" className="sr-only">Area, project or developer</label>
          <input id="hero-q" name="q" placeholder="Area, project or developer" autoComplete="off" className="min-w-0 flex-1 rounded-full bg-transparent px-5 py-3 text-[15px] outline-none placeholder:text-muted/70" />
          <label htmlFor="hero-beds" className="sr-only">Bedrooms</label>
          <select id="hero-beds" name="min_bedrooms" defaultValue="" className="rounded-full border-l border-line bg-transparent px-4 py-3 text-[15px] text-ink outline-none sm:border-l sm:py-2">
            <option value="">Any bedrooms</option>
            <option value="1">1+ bedrooms</option>
            <option value="2">2+ bedrooms</option>
            <option value="3">3+ bedrooms</option>
            <option value="4">4+ bedrooms</option>
          </select>
          <button type="submit" className={buttonClass("brass", "px-7 py-3")}>Search homes</button>
        </form>

        <ul className="mt-8 flex flex-wrap gap-x-7 gap-y-2 text-sm text-muted">
          <li className="flex items-center gap-2"><Tick />Free to use</li>
          <li className="flex items-center gap-2"><Tick />Answers in English and Amharic</li>
          <li className="flex items-center gap-2"><Tick />A broker behind every chat</li>
        </ul>
      </div>
      <div className="rise [animation-delay:120ms]"><HeroArt unit={unit} /></div>
    </section>
  );
}

function Tick() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4 text-accent" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m4 10.5 4 4 8-9" /></svg>
  );
}

/* ---------------- trust strip ---------------- */

export function TrustStrip({ stats }: { stats: Featured["stats"] | null }) {
  const items = [
    { value: stats ? stats.units.toLocaleString("en-US") : "Hundreds", label: "homes listed" },
    { value: stats ? String(stats.developers) : "Many", label: "developers and portals" },
    { value: stats ? String(stats.areas) : "Dozens", label: "neighbourhoods covered" },
    { value: "2", label: "languages: English and Amharic" },
  ];
  return (
    <section aria-label="At a glance" className="border-y border-line bg-white/60">
      <dl className={`${wrap} grid grid-cols-2 gap-y-8 py-10 md:grid-cols-4`}>
        {items.map((i) => (
          <div key={i.label} className="text-center">
            <dt className="sr-only">{i.label}</dt>
            <dd className="display text-4xl font-medium tnum">{i.value}</dd>
            <dd className="mt-1 text-sm text-muted">{i.label}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/* ---------------- featured homes ---------------- */

export function FeaturedHomes({ units }: { units: PortalUnit[] }) {
  return (
    <section id="homes" className={`${wrap} scroll-mt-20 py-20 lg:py-28`}>
      <div className="flex flex-wrap items-end justify-between gap-6">
        <div className="max-w-2xl">
          <Eyebrow>Featured homes</Eyebrow>
          <h2 className="display mt-4 text-4xl font-medium leading-tight sm:text-5xl">Homes worth a closer look</h2>
          <p className="mt-4 text-lg text-muted">A fresh selection from different developers each time you visit. Create a free account to see every detail and ask about any home.</p>
        </div>
        <Link href="/portal" className={buttonClass("outline")}>Browse all homes</Link>
      </div>
      {units.length > 0 ? (
        <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {units.map((u) => <ListingCard key={u.id} unit={u} />)}
        </div>
      ) : (
        <div className="mt-12 rounded-2xl border border-dashed border-line bg-white/50 px-6 py-14 text-center">
          <p className="display text-2xl">Our listings are loading</p>
          <p className="mx-auto mt-2 max-w-md text-muted">We could not reach the listings just now. Please try again in a moment, or sign in to browse.</p>
        </div>
      )}
    </section>
  );
}

/* ---------------- how it works ---------------- */

const STEPS = [
  { n: "01", title: "Browse with confidence", text: "Every listing from our partner developers and portals sits in one place, with prices, sizes, payment plans and delivery dates side by side." },
  { n: "02", title: "Ask the assistant", text: "Tell it your budget and the area. It shortlists, compares homes, works out what you can afford and answers in English or Amharic, day or night." },
  { n: "03", title: "Visit with our broker", text: "Pick a time and your visit is booked, with the route planned. Our broker can step into the conversation whenever you want a person." },
];

export function HowItWorks() {
  return (
    <section id="how" className="scroll-mt-20 bg-raised/60">
      <div className={`${wrap} py-20 lg:py-28`}>
        <div className="max-w-2xl">
          <Eyebrow>How it works</Eyebrow>
          <h2 className="display mt-4 text-4xl font-medium leading-tight sm:text-5xl">From first look to front door, in three steps</h2>
        </div>
        <ol className="mt-14 grid gap-6 md:grid-cols-3">
          {STEPS.map((s) => (
            <li key={s.n} className="rounded-2xl border border-line bg-white p-8">
              <span className="display text-5xl font-medium text-accent/70">{s.n}</span>
              <h3 className="display mt-6 text-2xl font-medium">{s.title}</h3>
              <p className="mt-3 leading-relaxed text-muted">{s.text}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/* ---------------- the assistant ---------------- */

const CAPABILITIES = [
  "Shortlists homes that match your budget, area and bedrooms",
  "Compares two or three homes side by side",
  "Works out what a payment plan means for your monthly budget",
  "Shows a 3D layout when a home has one",
  "Books a visit and plans the route for you",
  "Hands the chat to our broker whenever you ask",
];

export function Assistant() {
  return (
    <section id="assistant" className={`${wrap} scroll-mt-20 py-20 lg:py-28`}>
      <div className="grid items-center gap-12 overflow-hidden rounded-[2rem] bg-ink px-6 py-12 text-white sm:px-12 lg:grid-cols-2 lg:py-16">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#e0bf86]">The assistant</p>
          <h2 className="display mt-4 text-4xl font-medium leading-tight sm:text-5xl">Ask anything. Get a straight answer.</h2>
          <p className="mt-5 max-w-lg text-lg leading-relaxed text-white/70">No forms and no waiting for office hours. It knows every home we list and tells you plainly when it is not sure, so a broker can step in.</p>
          <ul className="mt-8 space-y-3">
            {CAPABILITIES.map((c) => (
              <li key={c} className="flex items-start gap-3 text-[15px] text-white/85">
                <svg viewBox="0 0 20 20" className="mt-0.5 h-4 w-4 shrink-0 text-[#e0bf86]" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m4 10.5 4 4 8-9" /></svg>
                {c}
              </li>
            ))}
          </ul>
          <Link href="/signup" className={buttonClass("light", "mt-10")}>Start a conversation</Link>
        </div>
        <div className="rounded-3xl bg-white/[0.06] p-5 ring-1 ring-white/10" aria-label="Example conversation">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-white/50">Example conversation</p>
          <div className="mt-4 space-y-3 text-[15px]">
            <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-[#e0bf86] px-4 py-2.5 text-ink">I have about 25 million birr. Something with 3 bedrooms in Bole.</p>
            <p className="w-fit max-w-[88%] rounded-2xl rounded-bl-md bg-white/10 px-4 py-2.5">Good news, there are homes in that range. I can show the closest matches and what the payment plans look like. Do you want to buy to live in, or to rent out?</p>
            <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-[#e0bf86] px-4 py-2.5 text-ink">To live in. Compare the first two for me.</p>
            <p className="w-fit max-w-[88%] rounded-2xl rounded-bl-md bg-white/10 px-4 py-2.5">Of course. I will line up the price, size, down payment and delivery date for both. Shall I also book a visit?</p>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ---------------- areas ---------------- */

export function Areas({ areas }: { areas: Featured["sub_cities"] }) {
  if (areas.length === 0) return null;
  return (
    <section id="areas" className={`${wrap} scroll-mt-20 pb-20 lg:pb-28`}>
      <div className="max-w-2xl">
        <Eyebrow>Browse by area</Eyebrow>
        <h2 className="display mt-4 text-4xl font-medium leading-tight sm:text-5xl">Where would you like to live?</h2>
      </div>
      <div className="mt-10 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {areas.map((a) => (
          <Link key={a.sub_city} href={`/portal?sub_city=${encodeURIComponent(a.sub_city)}`} className="group flex items-center justify-between rounded-2xl border border-line bg-white px-6 py-5 transition hover:border-accent/60 hover:shadow-md">
            <span className="display text-xl font-medium">{a.sub_city}</span>
            <span className="text-sm text-muted tnum group-hover:text-accent">{a.units} homes</span>
          </Link>
        ))}
      </div>
    </section>
  );
}

/* ---------------- closing call to action ---------------- */

export function FinalCta() {
  return (
    <section className="bg-gradient-to-b from-raised/0 to-raised/80">
      <div className={`${wrap} py-20 text-center lg:py-24`}>
        <h2 className="display mx-auto max-w-3xl text-4xl font-medium leading-tight sm:text-5xl">Ready to find your next home?</h2>
        <p className="mx-auto mt-4 max-w-xl text-lg text-muted">Create a free account in a minute. See every detail, save the homes you like and talk to the assistant whenever you wish.</p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Link href="/signup" className={buttonClass("primary", "px-7 py-3")}>Create your free account</Link>
          <Link href="/login" className={buttonClass("outline", "px-7 py-3")}>Sign in</Link>
        </div>
      </div>
    </section>
  );
}
