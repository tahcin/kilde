import { motion, useReducedMotion } from "motion/react";
import { ArrowRight, Check, FileSearch, Fingerprint, Plus, ScanSearch, ShieldQuestion } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { InlineSearch, rowLine } from "../components/Search";
import { useCompare } from "../lib/compare";
import { useIndex, useMeta, type Row } from "../lib/data";
import { cn, displayName, industry, titleCase } from "../lib/format";
import { Dropdown } from "../components/Dropdown";
import { Money } from "../components/Money";
import { useTitle } from "../lib/title";
import { FAM, FamChip, type Fam } from "../lib/family";

/** Well-known companies from Builderr's own sample, chosen because their profiles show most kinds of fact. */
export const TRY = ["938702675", "999296432", "976605713", "979543883", "976695372", "990167826", "966820152", "982769221"];
const ease = [0.16, 1, 0.3, 1] as const;

function Hero() {
  const { data: rows } = useIndex();
  const tries = useMemo(() => TRY.map((o) => rows?.find((r) => r.o === o)).filter(Boolean) as Row[], [rows]);
  const reduce = useReducedMotion();
  const rise = (d: number) => (reduce ? {} : { initial: { opacity: 0, y: 18, filter: "blur(8px)" }, animate: { opacity: 1, y: 0, filter: "blur(0px)" }, transition: { duration: 1, delay: d, ease } });
  return (
    <section className="relative isolate overflow-hidden" aria-labelledby="hero-title">
      <div className="ruled absolute inset-0 -z-10" aria-hidden />
      <div className="wrap flex flex-col items-center pb-14 pt-14 text-center sm:pb-16 sm:pt-24">
        <motion.h1 id="hero-title" {...rise(0.05)} className="display max-w-[16ch] text-[2.5rem] text-ink sm:text-[3.6rem] lg:text-[4.3rem]">
          Norwegian companies, with the source of every fact.
        </motion.h1>
        <motion.p {...rise(0.15)} className="mt-5 max-w-[36rem] text-[17px] leading-relaxed text-ink-2 sm:text-lg">
          Kilde reads the public registers and each company's own website, and keeps the exact page and words behind everything it tells you. What it cannot prove, it says so.
        </motion.p>
        <motion.div {...rise(0.25)} className="mt-9 w-full max-w-2xl text-left">
          <InlineSearch autoFocus={false} />
        </motion.div>
        <motion.div {...rise(0.35)} className="mt-5 flex max-w-2xl flex-wrap items-center justify-center gap-2">
          <span className="mr-1 text-[13px] text-muted">Try</span>
          {tries.slice(0, 5).map((r) => (
            <Link key={r.o} to={`/c/${r.o}`} className="rounded-full border border-rule bg-card px-3 py-1.5 text-[13px] font-medium text-ink-2 transition hover:border-brand/40 hover:bg-wash hover:text-brand">
              {displayName(r.n)}
            </Link>
          ))}
        </motion.div>
      </div>
    </section>
  );
}

function Numbers() {
  const { data: meta } = useMeta();
  const items: [string, string][] = [
    [meta ? meta.companies.toLocaleString("en-GB") : "…", "Norwegian companies on this site: 1,000 drawn at random, two 100-company test samples, and Builderr's own sample of 100."],
    [meta ? meta.facts.toLocaleString("en-GB") : "…", "published facts, every one linked to the page it came from, when it was read, and the exact words."],
    ["0", "wrong-company facts, invented figures or wrong values in the hand audit of the final code, on a fresh random sample checked against the live sources."],
    ["$0", "to build a profile: open public sources only, and no language model anywhere in the agent."],
  ];
  return (
    <section aria-label="Kilde in numbers" className="-mb-16 border-t border-rule bg-card/60">
      <div className="wrap grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
        {items.map(([n, t], i) => (
          <div key={t} className={cn("border-rule/80 py-7", i > 0 && "border-t sm:border-t-0", i % 2 === 1 && "sm:border-l sm:pl-6", i === 2 && "sm:border-t sm:pt-7 lg:border-t-0", i === 3 && "sm:border-t lg:border-t-0", i > 0 && "lg:border-l lg:pl-6", "lg:pr-6")}>
            <p className="text-[2.3rem] font-semibold leading-none tracking-[-0.04em] text-ink">{n}</p>
            <p className="mt-3 text-[13.5px] leading-snug text-muted">{t}</p>
          </div>
        ))}
      </div>
      <div className="wrap py-6">
        <Link to="/how" className="group inline-flex items-center gap-1.5 text-[14.5px] font-medium text-brand">
          How Kilde finds, proves and sources every fact<ArrowRight className="size-4 transition group-hover:translate-x-0.5" aria-hidden />
        </Link>
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

type Filter = "all" | "w" | "s" | "h" | "n";
const FILTERS: [Filter, string, Fam | null][] = [["all", "All", null], ["w", "Verified website", "web"], ["s", "Social profiles", "social"], ["h", "Hiring", "hiring"], ["n", "News", "news"]];

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
  const [inds, setInds] = useState<string[]>([]);
  const [towns, setTowns] = useState<string[]>([]);
  const [shown, setShown] = useState(30);
  const indOf = (r: Row) => industry(r.ic, r.in) || "Not stated";
  const townOf = (r: Row) => (r.m ? titleCase(r.m) : "Not stated");
  const opts = (key: (r: Row) => string) => {
    const c = new Map<string, number>();
    rows?.forEach((r) => c.set(key(r), (c.get(key(r)) ?? 0) + 1));
    return [...c].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([v, n]) => ({ value: v, label: v, count: n }));
  };
  const indOpts = useMemo(() => opts(indOf), [rows]);  // eslint-disable-line react-hooks/exhaustive-deps
  const townOpts = useMemo(() => opts(townOf), [rows]);  // eslint-disable-line react-hooks/exhaustive-deps
  const list = useMemo(() => {
    if (!rows) return [];
    const xs = rows.filter((r) => hasFamily(r, filter) && (!inds.length || inds.includes(indOf(r))) && (!towns.length || towns.includes(townOf(r))));
    // Figures are never converted, so NOK filers are ranked first and other currencies after them.
    if (sort === "rev") return [...xs].sort((a, b) => Number(b.rev?.c === "NOK") - Number(a.rev?.c === "NOK") || Number(!!b.rev) - Number(!!a.rev) || (b.rev?.a ?? 0) - (a.rev?.a ?? 0));
    if (sort === "facts") return [...xs].sort((a, b) => b.nf - a.nf);
    return xs;
  }, [rows, filter, sort, inds, towns]);  // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <section className="wrap pb-20 pt-2" aria-labelledby="explore-title">
      <h2 id="explore-title" className="display text-[2rem] text-ink sm:text-[2.6rem]">Browse every company</h2>
      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Show only">
          {FILTERS.map(([k, t, fam]) => {
            const on = filter === k, f = fam ? FAM[fam] : null;
            return (
              <button key={k} onClick={() => { setFilter(k); setShown(30); }} aria-pressed={on}
                className={cn("flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[13.5px] font-medium ring-1 transition",
                  on ? (f ? cn(f.bg, f.ink, "ring-current/25") : "bg-brand text-on ring-brand") : "bg-card text-ink-2 ring-rule hover:ring-brand/40")}>
                {f && <span className={cn("size-2 rounded-full", f.dot)} aria-hidden />}
                {t}{rows && k !== "all" && <span className={cn(on ? "opacity-70" : "text-muted")}>{rows.filter((r) => hasFamily(r, k)).length}</span>}
              </button>
            );
          })}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Dropdown multiple label="Industry" searchable options={indOpts} value={inds} onChange={(v) => { setInds(v); setShown(30); }} />
          <Dropdown multiple label="Town" searchable options={townOpts} value={towns} onChange={(v) => { setTowns(v); setShown(30); }} />
          <Dropdown label="Sort" align="end" value={sort} onChange={(v) => setSort(v as typeof sort)}
            options={[{ value: "rev", label: "Latest revenue (NOK)" }, { value: "facts", label: "Most facts" }, { value: "name", label: "Name" }]} />
        </div>
      </div>
      <ul className="surface mt-5 divide-y divide-rule/70 overflow-hidden rounded-[1.3rem]">
        {!rows && Array.from({ length: 6 }, (_, i) => <li key={i} className="h-[4.5rem] animate-pulse bg-paper-2/30" />)}
        {rows && list.length === 0 && <li className="px-5 py-8 text-center text-[14px] text-muted">No companies match these filters.</li>}
        {list.slice(0, shown).map((r) => (
          <li key={r.o}>
            <Link to={`/c/${r.o}`} className="group flex items-center gap-3 px-4 py-3.5 transition hover:bg-wash/50 sm:gap-4 sm:px-5">
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[15.5px] font-medium text-ink group-hover:text-brand">{displayName(r.n)}</span>
                <span className="block truncate text-[13px] text-muted">{rowLine(r)}</span>
              </span>
              <span className="hidden gap-1 md:flex" aria-hidden>
                {r.f.w > 0 && <FamChip fam="web" />}{r.f.s > 0 && <FamChip fam="social" />}{(r.f.h > 0 || r.j > 0) && <FamChip fam="hiring" />}{r.f.n > 0 && <FamChip fam="news" />}
              </span>
              <span className="hidden w-40 text-right sm:block">
                {r.rev ? <><Money currency={r.rev.c} n={r.rev.a} className="block text-[14px] font-medium text-ink" /><span className="block text-[12px] text-muted">revenue {r.rev.y}{r.rev.t === "group" ? ", group" : ""}</span></>
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


export default function Home() {
  useTitle(null);
  return (
    <>
      <Hero />
      <Explore />
      <Numbers />
    </>
  );
}
