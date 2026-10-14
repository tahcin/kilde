import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ArrowRight, Check, FileSearch, Fingerprint, Plus, ScanSearch, ShieldQuestion } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { AskPanel } from "../components/Ask";
import { InlineSearch, rowLine } from "../components/Search";
import { render } from "../components/Value";
import { useCompare } from "../lib/compare";
import { useCompanies, useIndex, useMeta, type Claim, type Company, type Row } from "../lib/data";
import { amount, cn, dateTime, displayName, label, METHOD, sourceName } from "../lib/format";
import { useTitle } from "../lib/title";

/** Well-known companies from Builderr's own sample, chosen because their profiles show most kinds of fact. */
export const TRY = ["938702675", "999296432", "976605713", "979543883", "976695372", "990167826", "966820152", "982769221"];
const ease = [0.16, 1, 0.3, 1] as const;

/** Real facts from real profiles for the first screen, each with its receipt. */
const SPECIMENS: [string, string][] = [["938702675", "official_website"], ["999296432", "revenue"], ["976605713", "social_profile"], ["979543883", "dated_news"]];

function pickSpecimen(cos: Company[], [org, field]: [string, string]): { co: Company; claim: Claim } | null {
  const co = cos.find((c) => c.o === org);
  const claim = co?.c.find((c) => c.f === field && c.a === "available" && c.e.length && (field !== "revenue" || c.v?.account_type === "company"));
  return co && claim ? { co, claim } : null;
}

function Specimen() {
  const { data } = useCompanies(SPECIMENS.map(([o]) => o));
  const items = useMemo(() => (data ? SPECIMENS.map((s) => pickSpecimen(data, s)).filter(Boolean) as { co: Company; claim: Claim }[] : []), [data]);
  const [n, setN] = useState(0);
  const [held, setHeld] = useState(false);
  const reduce = useReducedMotion();
  useEffect(() => {
    if (held || reduce || items.length < 2) return;
    const t = setInterval(() => { if (!document.hidden) setN((x) => (x + 1) % items.length); }, 6500);
    return () => clearInterval(t);
  }, [held, reduce, items.length]);
  const it = items[n % Math.max(items.length, 1)];
  const ev = it ? it.co.ev[it.claim.e[0]] : null;
  return (
    <div className="relative" onPointerEnter={() => setHeld(true)} onPointerLeave={() => setHeld(false)} onFocus={() => setHeld(true)} onBlur={() => setHeld(false)}>
      <div className="surface relative flex min-h-[18.5rem] flex-col overflow-hidden rounded-[1.6rem]">
        {!it || !ev ? <div className="flex-1 animate-pulse bg-paper-2/40" /> : (
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={n} initial={{ opacity: 0, y: 14, filter: "blur(6px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              exit={{ opacity: 0, y: -10, filter: "blur(6px)" }} transition={{ duration: 0.6, ease }} className="flex flex-1 flex-col">
              <div className="px-6 pb-5 pt-6">
                <Link to={`/c/${it.co.o}`} className="text-[13px] font-medium text-brand hover:underline">{displayName(it.co.n)}</Link>
                <p className="mt-3 text-[13px] text-muted">{label(it.claim.f)}{it.claim.f === "revenue" ? `, ${it.claim.v.year} company accounts` : ""}</p>
                <p className="mt-1 text-[22px] font-semibold leading-tight tracking-[-0.02em] text-ink [&_a]:no-underline">
                  {it.claim.f === "revenue" ? <span className="tnum">{it.claim.v.currency} {amount(it.claim.v.amount)}</span> : render(it.claim.f, it.claim.v)}
                </p>
              </div>
              <div className="flex-1 border-t border-dashed border-rule bg-paper/60 px-6 pb-6 pt-4">
                <p className="flex items-center justify-between gap-3 text-[12.5px] text-muted">
                  <span className="font-medium text-ink-2">{sourceName(it.co.u[ev.u] ?? "", ev.k)}</span>
                  <span>{dateTime(ev.t)}</span>
                </p>
                <blockquote className="mt-3 line-clamp-4 font-mono text-[12.5px] leading-[1.75] text-ink">
                  <span className="quote-mark quote-sweep">{ev.q}</span>
                </blockquote>
                <p className="mt-3 text-[12.5px] leading-snug text-ink-2">{METHOD[it.claim.m ?? ""] ?? "Matched to this organisation number."}</p>
              </div>
            </motion.div>
          </AnimatePresence>
        )}
      </div>
      <div className="mt-3 flex items-center justify-between px-1">
        <p className="text-[12.5px] text-muted">Real facts from this site, each with its receipt.</p>
        <div className="flex gap-1.5" role="tablist" aria-label="Examples">
          {items.map((x, i) => (
            <button key={i} role="tab" aria-selected={i === n} aria-label={`${label(x.claim.f)} for ${displayName(x.co.n)}`} onClick={() => setN(i)}
              className={cn("h-1.5 rounded-full transition-all duration-500", i === n ? "w-6 bg-brand" : "w-1.5 bg-ink/20 hover:bg-ink/40")} />
          ))}
        </div>
      </div>
    </div>
  );
}

function Hero() {
  const { data: rows } = useIndex();
  const tries = useMemo(() => TRY.map((o) => rows?.find((r) => r.o === o)).filter(Boolean) as Row[], [rows]);
  const reduce = useReducedMotion();
  const rise = (d: number) => (reduce ? {} : { initial: { opacity: 0, y: 18, filter: "blur(8px)" }, animate: { opacity: 1, y: 0, filter: "blur(0px)" }, transition: { duration: 1, delay: d, ease } });
  return (
    <section className="relative isolate overflow-hidden" aria-labelledby="hero-title">
      <div className="ruled absolute inset-0 -z-10" aria-hidden />
      <div className="mx-auto grid max-w-6xl gap-12 px-4 pb-16 pt-12 sm:px-6 sm:pt-20 lg:grid-cols-[1.15fr_1fr] lg:items-center lg:gap-14 lg:pb-24">
        <div>
          <motion.h1 id="hero-title" {...rise(0.05)} className="display max-w-[15ch] text-[2.6rem] text-ink sm:text-[3.6rem] lg:text-[4.1rem]">
            Norwegian companies, with the source of every fact.
          </motion.h1>
          <motion.p {...rise(0.15)} className="mt-5 max-w-[34rem] text-[17px] leading-relaxed text-ink-2 sm:text-lg">
            Kilde reads the public registers and each company's own website, and keeps the exact page and words behind everything it tells you. What it cannot prove, it says so.
          </motion.p>
          <motion.div {...rise(0.25)} className="mt-8 max-w-xl">
            <InlineSearch autoFocus={false} />
          </motion.div>
          <motion.div {...rise(0.35)} className="mt-5 flex max-w-xl flex-wrap items-center gap-2">
            <span className="mr-1 text-[13px] text-muted">Try</span>
            {tries.slice(0, 5).map((r) => (
              <Link key={r.o} to={`/c/${r.o}`} className="rounded-full border border-rule bg-card px-3 py-1.5 text-[13px] font-medium text-ink-2 transition hover:border-brand/40 hover:bg-wash hover:text-brand">
                {displayName(r.n)}
              </Link>
            ))}
          </motion.div>
        </div>
        <motion.div {...rise(0.3)}><Specimen /></motion.div>
      </div>
    </section>
  );
}

function Numbers() {
  const { data: meta } = useMeta();
  const items: [string, string][] = [
    [meta ? meta.companies.toLocaleString("en-GB") : "…", "Norwegian companies on this site: 1,000 drawn at random, two 100-company test samples, and Builderr's own sample of 100."],
    [meta ? meta.facts.toLocaleString("en-GB") : "…", "published facts, every one linked to the page it came from, when it was read, and the exact words."],
    ["0", "wrong-company facts in the last three audit rounds, each a fresh random sample checked by hand against the live sources."],
    ["$0", "to build a profile: open public sources only, and no language model anywhere in the agent."],
  ];
  return (
    <section aria-label="Kilde in numbers" className="border-y border-rule bg-card/60">
      <div className="mx-auto grid max-w-6xl grid-cols-1 gap-px bg-rule/70 sm:grid-cols-2 lg:grid-cols-4">
        {items.map(([n, t]) => (
          <div key={t} className="bg-paper px-5 py-7 sm:px-6">
            <p className="text-[2.3rem] font-semibold leading-none tracking-[-0.04em] text-ink">{n}</p>
            <p className="mt-3 text-[13.5px] leading-snug text-muted">{t}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

export const STEPS = [
  { icon: FileSearch, title: "Start from the organisation number", text: "Registration, accounts, roles, workplaces and public records are read from registers by the company's 9-digit number, so they cannot belong to anyone else." },
  { icon: ScanSearch, title: "Prove the website before using it", text: "A site counts only if it prints the org number, or shows the registered phone or address with the name, and no other company shares that name. Only then are its social profiles, news and careers page used." },
  { icon: Fingerprint, title: "Keep the receipt", text: "Every fact keeps the address it came from, when it was read, a fingerprint of the saved page and the exact words, so anyone can check it in one click." },
  { icon: ShieldQuestion, title: "Say what is missing", text: "When something is not there, or cannot be proven, the profile says so and why. A missing website is never shown as no website, and no figure is ever estimated." },
];

function HowItWorks() {
  return (
    <section className="mx-auto max-w-6xl px-4 py-20 sm:px-6" aria-labelledby="how-title">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h2 id="how-title" className="display max-w-[22ch] text-[2rem] text-ink sm:text-[2.6rem]">Every fact has a source you can open.</h2>
        <Link to="/how" className="group flex items-center gap-1.5 text-[14.5px] font-medium text-brand">The full method<ArrowRight className="size-4 transition group-hover:translate-x-0.5" aria-hidden /></Link>
      </div>
      <ol className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((s, i) => (
          <li key={s.title} className="surface rounded-[1.3rem] p-5">
            <div className="flex items-center justify-between">
              <s.icon className="size-5 text-brand" aria-hidden />
              <span className="tnum text-[12px] font-semibold text-muted">{i + 1}</span>
            </div>
            <h3 className="mt-5 text-[16px] font-semibold leading-snug tracking-[-0.01em] text-ink">{s.title}</h3>
            <p className="mt-2 text-[14px] leading-relaxed text-ink-2">{s.text}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

function AskHome() {
  const { picked } = useCompare();
  const [orgs, setOrgs] = useState<string[]>(() => (picked.length ? picked : ["938702675"]));
  const { data: cos } = useCompanies(orgs);
  const { data: rows } = useIndex();
  const name = (o: string) => displayName(rows?.find((r) => r.o === o)?.n ?? o);
  return (
    <section className="bg-night text-white" aria-labelledby="ask-title">
      <div className="mx-auto grid max-w-6xl gap-10 px-4 py-20 sm:px-6 lg:grid-cols-[1fr_1.1fr] lg:items-start">
        <div>
          <h2 id="ask-title" className="display max-w-[16ch] text-[2rem] sm:text-[2.6rem]">Ask questions. Get answers with sources.</h2>
          <p className="mt-4 max-w-md text-[16px] leading-relaxed text-white/75">
            Pick up to three companies and ask in English or Norwegian. Claude answers from Kilde's facts alone and numbers every source; if the facts do not say, neither does the answer.
          </p>
          <div className="mt-8 max-w-md">
            <div className="flex flex-wrap gap-1.5">
              {orgs.map((o) => (
                <span key={o} className="flex items-center gap-1 rounded-full bg-white/10 py-1 pl-3 pr-1 text-[13px]">
                  {name(o)}
                  <button onClick={() => setOrgs(orgs.filter((x) => x !== o))} className="grid size-5 place-items-center rounded-full text-white/70 hover:bg-white/15 hover:text-white" aria-label={`Remove ${name(o)}`}>×</button>
                </span>
              ))}
            </div>
            {orgs.length < 3 && (
              <div className="mt-3 text-ink">
                <InlineSearch size="md" exclude={orgs} placeholder={orgs.length ? "Add a company to compare" : "Pick a company"} onPick={(r) => setOrgs([...orgs, r.o])} />
              </div>
            )}
          </div>
        </div>
        {cos && cos.length > 0 ? (
          <AskPanel companies={cos} className="text-ink"
            suggestions={cos.length > 1 ? ["Which is larger by revenue?", "Who runs each company?", "Which ones are hiring?", "Hvem har verifisert nettside?"]
              : ["What does it do?", "Who runs it?", "How did it do in its latest accounts?", "Hva har endret seg nylig?"]} />
        ) : (
          <div className="surface grid min-h-60 place-items-center rounded-[1.4rem] p-6 text-center text-[15px] text-muted">{orgs.length ? "Loading…" : "Pick a company to ask about."}</div>
        )}
      </div>
    </section>
  );
}

type Filter = "all" | "w" | "s" | "h" | "n";
const FILTERS: [Filter, string][] = [["all", "All"], ["w", "Verified website"], ["s", "Social profiles"], ["h", "Hiring"], ["n", "News"]];

export function CompareToggle({ o, className }: { o: string; className?: string }) {
  const { picked, toggle } = useCompare();
  const on = picked.includes(o);
  const [full, setFull] = useState(false);
  return (
    <button onClick={(e) => { e.preventDefault(); e.stopPropagation(); setFull(!toggle(o)); setTimeout(() => setFull(false), 2200); }} aria-pressed={on}
      title={full ? "You can compare up to three companies" : on ? "Remove from compare" : "Add to compare"}
      className={cn("flex h-8 shrink-0 items-center gap-1 rounded-full border px-2.5 text-[12.5px] font-medium transition",
        on ? "border-brand/40 bg-wash text-brand" : full ? "border-amber/40 bg-amber-soft text-amber" : "border-rule bg-card text-ink-2 hover:border-brand/40 hover:text-brand", className)}>
      {on ? <Check className="size-3.5" aria-hidden /> : <Plus className="size-3.5" aria-hidden />}
      <span>{full ? "Max 3" : on ? "Picked" : "Compare"}</span>
    </button>
  );
}

function hasFamily(r: Row, f: Filter) { return f === "all" ? true : f === "h" ? r.f.h > 0 || r.j > 0 : r.f[f] > 0; }

function Explore() {
  const { data: rows } = useIndex();
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<"name" | "rev" | "facts">("rev");
  const [shown, setShown] = useState(30);
  const list = useMemo(() => {
    if (!rows) return [];
    const xs = rows.filter((r) => hasFamily(r, filter));
    // Figures are never converted, so NOK filers are ranked first and other currencies after them.
    if (sort === "rev") return [...xs].sort((a, b) => Number(b.rev?.c === "NOK") - Number(a.rev?.c === "NOK") || Number(!!b.rev) - Number(!!a.rev) || (b.rev?.a ?? 0) - (a.rev?.a ?? 0));
    if (sort === "facts") return [...xs].sort((a, b) => b.nf - a.nf);
    return xs;
  }, [rows, filter, sort]);
  return (
    <section className="mx-auto max-w-6xl px-4 pt-20 sm:px-6" aria-labelledby="explore-title">
      <h2 id="explore-title" className="display text-[2rem] text-ink sm:text-[2.6rem]">Browse every company</h2>
      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Show only">
          {FILTERS.map(([k, t]) => (
            <button key={k} onClick={() => { setFilter(k); setShown(30); }} aria-pressed={filter === k}
              className={cn("rounded-full px-3.5 py-1.5 text-[13.5px] font-medium transition", filter === k ? "bg-ink text-white" : "bg-card text-ink-2 ring-1 ring-rule hover:ring-brand/40")}>
              {t}{rows && k !== "all" && <span className={cn("ml-1.5 tnum", filter === k ? "text-white/60" : "text-muted")}>{rows.filter((r) => hasFamily(r, k)).length}</span>}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-[13.5px] text-muted">Sort by
          <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)} className="rounded-lg border border-rule bg-card px-2.5 py-1.5 text-ink">
            <option value="rev">Latest revenue (NOK)</option><option value="facts">Most facts</option><option value="name">Name</option>
          </select>
        </label>
      </div>
      <ul className="surface mt-5 divide-y divide-rule/70 overflow-hidden rounded-[1.3rem]">
        {!rows && Array.from({ length: 6 }, (_, i) => <li key={i} className="h-[4.5rem] animate-pulse bg-paper-2/30" />)}
        {list.slice(0, shown).map((r) => (
          <li key={r.o}>
            <Link to={`/c/${r.o}`} className="group flex items-center gap-3 px-4 py-3.5 transition hover:bg-wash/50 sm:gap-4 sm:px-5">
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[15.5px] font-medium text-ink group-hover:text-brand">{displayName(r.n)}</span>
                <span className="block truncate text-[13px] text-muted">{rowLine(r)}</span>
              </span>
              <span className="hidden gap-1 md:flex" aria-hidden>
                {r.f.w > 0 && <Tag>Website</Tag>}{r.f.s > 0 && <Tag>Social</Tag>}{(r.f.h > 0 || r.j > 0) && <Tag>Hiring</Tag>}{r.f.n > 0 && <Tag>News</Tag>}
              </span>
              <span className="hidden w-40 text-right sm:block">
                {r.rev ? <><span className="tnum block text-[14px] font-medium text-ink">{r.rev.c} {amount(r.rev.a)}</span><span className="block text-[12px] text-muted">revenue {r.rev.y}{r.rev.t === "group" ? ", group" : ""}</span></>
                  : <span className="text-[12.5px] text-muted">No revenue filed</span>}
              </span>
              <CompareToggle o={r.o} />
            </Link>
          </li>
        ))}
      </ul>
      {list.length > shown && (
        <div className="mt-5 flex justify-center">
          <button onClick={() => setShown(shown + 60)} className="rounded-full border border-rule bg-card px-5 py-2.5 text-[14px] font-medium text-ink-2 transition hover:border-brand/40 hover:text-brand">
            Show more ({(list.length - shown).toLocaleString("en-GB")} left)
          </button>
        </div>
      )}
    </section>
  );
}

const Tag = ({ children }: { children: string }) => <span className="rounded-md bg-paper-2 px-1.5 py-0.5 text-[11.5px] font-medium text-ink-2">{children}</span>;

export default function Home() {
  useTitle(null);
  return (
    <>
      <Hero />
      <Numbers />
      <HowItWorks />
      <AskHome />
      <Explore />
    </>
  );
}
