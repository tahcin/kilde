// Up to three companies side by side: one aligned table, companies as columns, a source behind every value.
// Bars only visualise figures Kilde claimed (scaled to the largest in the row); no figure is computed from another.
import { ChevronDown, Plus, X } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { useAskScope } from "../components/AskLauncher";
import { InlineSearch } from "../components/Search";
import { SourcePop } from "../components/Source";
import { newsParts, socialParts } from "../components/Value";
import { MAX_PICK, useCompare } from "../lib/compare";
import { anyOf, avail, latest, one, useCompanies, type Claim, type Company, type State } from "../lib/data";
import { FAM, type Fam } from "../lib/family";
import { LEGAL_FORM, STATE, bare, cn, date, displayName, employees, industry, moneyShort, plural, titleCase } from "../lib/format";
import { useTitle } from "../lib/title";

/** One cell: the claim behind it (for the source), what to show, a key for "only differences", and a number for bars. */
type Cell = { claim: Claim | null; body: ReactNode; key: string; num?: { v: number; cur: string; period: string } ; missing?: State | "none" };
type RowDef = { name: string; get: (co: Company) => Cell; bars?: boolean };
type Group = { id: string; title: string; fam?: Fam; rows: RowDef[] };

const MISSING_TONE: Record<string, string> = {
  not_available: "bg-none-soft text-none", none: "bg-none-soft text-none", not_applicable: "bg-none-soft text-none",
  ambiguous: "bg-amber-soft text-amber", blocked: "bg-amber-soft text-amber", failed: "bg-bad-soft text-bad",
};

/** A field Kilde did not find: its own state in words, never a zero. */
function missing(co: Company, f: string, word?: string): Cell {
  const c = anyOf(co, f).find((x) => x.a !== "available") ?? null;
  const st: State | "none" = c ? c.a : "none";
  const text = c ? (c.a === "not_available" && word ? word : STATE[c.a].word) : "No record";
  return { claim: c && c.e.length ? c : null, missing: st, key: `missing:${st}`,
    body: <span className={cn("inline-flex rounded-full px-2 py-0.5 text-[12px] font-medium", MISSING_TONE[st])}>{text}</span> };
}
const fact = (f: string, show: (c: Claim) => ReactNode = (c) => String(c.v), key?: (c: Claim) => string, word?: string) => (co: Company): Cell => {
  const c = one(co, f);
  return c ? { claim: c, body: show(c), key: key ? key(c) : JSON.stringify(c.v) } : missing(co, f, word);
};
const money = (f: string) => (co: Company): Cell => {
  const c = latest(co, f);
  if (!c) return missing(co, f, "Not filed");
  const period = `${c.v.year}|${c.v.account_type ?? "company"}|${c.v.period_start ?? ""}|${c.v.period_end ?? ""}`;
  return { claim: c, key: `${c.v.currency}${c.v.amount}`, num: { v: Number(c.v.amount), cur: c.v.currency ?? "", period },
    body: <><span className={cn("font-semibold", c.v.amount < 0 && "text-bad")}>{moneyShort(c.v.currency, c.v.amount).replace(/\u202f/g, "\u2009")}</span>
      <span className="block text-[11.5px] text-muted">{c.v.year}{c.v.account_type === "group" ? ", group" : ""}</span></> };
};
const count = (f: string, noun: string, extra?: (cs: Claim[]) => ReactNode, word?: string) => (co: Company): Cell => {
  const cs = avail(co, f);
  return cs.length ? { claim: cs[0], key: `n${cs.length}`, body: <>{plural(cs.length, noun)}{extra?.(cs)}</> } : missing(co, f, word);
};

const GROUPS: Group[] = [
  { id: "key", title: "Key figures", fam: "accounts", rows: [
    { name: "Revenue", get: money("revenue"), bars: true },
    { name: "Operating result", get: money("operating_result"), bars: true },
    { name: "Annual result", get: money("annual_result"), bars: true },
    { name: "Equity", get: money("equity"), bars: true },
    { name: "Total assets", get: money("total_assets"), bars: true },
    { name: "Employees", bars: true, get: (co) => {
      const c = one(co, "employees_registered");
      if (!c) return missing(co, "employees_registered", "None registered");
      return { claim: c, key: String(c.v), body: <span className="font-semibold">{employees(c.v)}</span>,
        num: typeof c.v === "number" ? { v: c.v, cur: "people", period: "" } : undefined };
    } },
  ] },
  { id: "people", title: "Leadership", fam: "people", rows: [
    { name: "General manager", get: fact("ceo", (c) => c.v.name, (c) => c.v.name) },
    { name: "Board chair", get: fact("board_chair", (c) => c.v.name, (c) => c.v.name) },
    { name: "Board members", get: count("board_member", "member") },
    { name: "Auditor", get: fact("auditor", (c) => displayName(c.v.name), (c) => c.v.name) },
  ] },
  { id: "web", title: "Web presence", fam: "web", rows: [
    { name: "Verified website", get: fact("official_website", (c) => bare(c.v)) },
    { name: "Social profiles", get: (co) => {
      const cs = avail(co, "social_profile");
      return cs.length ? { claim: cs[0], key: cs.map((c) => socialParts(c.v).platform).join(","), body: cs.map((c) => socialParts(c.v).platform).join(", ") } : missing(co, "social_profile");
    } },
    { name: "Contact email", get: fact("site_contact_email", (c) => <span className="[overflow-wrap:anywhere]">{c.v}</span>) },
  ] },
  { id: "hiring", title: "Hiring and news", fam: "news", rows: [
    { name: "Careers page", get: fact("hiring_signal", (c) => <span className="[overflow-wrap:anywhere]">{bare(c.v)}</span>) },
    { name: "Job ads on NAV", get: count("job_ad", "active ad", undefined, "None active") },
    { name: "News posts", get: count("dated_news", "post", (cs) => {
      const last = [...cs].sort((a, b) => (b.d ?? "").localeCompare(a.d ?? ""))[0];
      return <span className="block text-[11.5px] text-muted">Latest {last.d ? date(last.d) : ""}: {newsParts(last.v).title}</span>;
    }) },
    { name: "Registry updates, 12 months", get: count("registry_change", "update") },
  ] },
  { id: "records", title: "Public records", fam: "records", rows: [
    { name: "Public contracts won", get: count("public_contract_award", "contract") },
    { name: "Research projects", get: count("research_project", "project") },
    { name: "State aid awards", get: count("state_aid_award", "award") },
    { name: "Food inspections", get: count("food_inspection", "inspection") },
    { name: "Building approval (DiBK)", get: fact("approval_sentral_godkjenning", (c) => (c.v.status === "approved" ? `Approved${c.v.valid_to ? ` until ${date(c.v.valid_to)}` : ""}` : c.v.status)) },
  ] },
  { id: "reg", title: "Registration", rows: [
    { name: "Legal form", get: fact("legal_form", (c) => LEGAL_FORM[c.v.code] ?? c.v.name, (c) => c.v.code) },
    { name: "Municipality", get: fact("municipality", (c) => titleCase(c.v.name), (c) => c.v.name) },
    { name: "Industry", get: fact("industry", (c) => <>{industry(c.v.code, c.v.name)}<span className="block text-[11.5px] text-muted">{c.v.code}</span></>, (c) => c.v.code) },
    { name: "Founded", get: fact("incorporation_date", (c) => date(c.v)) },
    { name: "Share capital", get: fact("share_capital", (c) => moneyShort(c.v.currency, c.v.amount)) },
    { name: "Parent company", get: fact("group_parent", (c) => displayName(c.v.name), (c) => c.v.organisation_number, "None registered") },
  ] },
];

function Bar({ v, max, top }: { v: number; max: number; top: boolean }) {
  const w = max > 0 ? Math.max(2, (Math.abs(v) / max) * 100) : 0;
  return (
    <span className="mt-1.5 flex items-center gap-1.5" aria-hidden>
      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-paper-2">
        <span className={cn("block h-full rounded-full", v < 0 ? "bg-bad/50" : "bg-fam-accounts-ink/60")} style={{ width: `${w}%` }} />
      </span>
      {top && <span className="shrink-0 rounded-full bg-fam-accounts px-1.5 text-[10.5px] font-semibold text-fam-accounts-ink">Highest</span>}
    </span>
  );
}

function CellView({ co, cell, bar }: { co: Company; cell: Cell; bar?: ReactNode }) {
  const inner = <span className="block w-full min-w-0 text-[13.5px] leading-snug text-ink [overflow-wrap:anywhere] sm:text-[14px]">{cell.body}{bar}</span>;
  if (!cell.claim) return <div className="min-w-0 px-2.5 py-2.5 sm:px-3">{inner}</div>;
  return (
    <SourcePop co={co} claim={cell.claim} className="group flex w-full min-w-0 flex-col items-start rounded-xl px-2.5 py-2.5 text-left transition hover:bg-wash/70 data-[state=open]:bg-wash sm:px-3">
      {inner}
      <span className="mt-1 flex items-center gap-1 text-[11px] text-muted opacity-0 transition group-hover:opacity-100 group-focus-visible:opacity-100 [@media(hover:none)]:opacity-60"><span className="size-1.5 rounded-full bg-pop" aria-hidden />Source</span>
    </SourcePop>
  );
}

function Slot({ co, onRemove }: { co: Company; onRemove: () => void }) {
  const lf = one(co, "legal_form"), mun = one(co, "municipality"), web = one(co, "official_website");
  return (
    <div className="relative min-w-0 rounded-2xl bg-card px-3 py-3 ring-1 ring-rule sm:px-4">
      <Link to={`/c/${co.o}`} className="block pr-7 text-[15px] font-semibold leading-snug text-ink hover:text-brand">{displayName(co.n)}</Link>
      <p className="mt-0.5 truncate text-[12px] text-muted">{[lf && (LEGAL_FORM[lf.v.code] ?? lf.v.code), mun && titleCase(mun.v.name)].filter(Boolean).join(" · ")}</p>
      <p className="truncate text-[12px] text-fam-web-ink">{web ? bare(web.v) : <span className="text-muted">No verified website</span>}</p>
      <button onClick={onRemove} className="absolute right-2 top-2 grid size-7 place-items-center rounded-full text-muted transition hover:bg-hover hover:text-ink" aria-label={`Remove ${displayName(co.n)}`}>
        <X className="size-4" />
      </button>
    </div>
  );
}

export default function Compare() {
  const [params, setParams] = useSearchParams();
  const { picked, set } = useCompare();
  const fromUrl = (params.get("o") ?? "").split(",").filter((o) => /^\d{9}$/.test(o)).slice(0, MAX_PICK);
  // A shared link names its companies in the address; they become this browser's picks too.
  useEffect(() => { if (fromUrl.length && fromUrl.join(",") !== picked.join(",")) set(fromUrl); }, [params]);  // eslint-disable-line react-hooks/exhaustive-deps
  const orgs = fromUrl.length ? fromUrl : picked;
  const update = (os: string[]) => { set(os); setParams(os.length ? { o: os.join(",") } : {}, { replace: true }); };
  const { data: cos } = useCompanies(orgs);
  useTitle(cos?.length ? `Compare ${cos.map((c) => displayName(c.n)).join(", ")}` : "Compare");
  const [diffOnly, setDiffOnly] = useState(false);
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  const n = cos?.length ?? 0;
  useAskScope(cos);

  const table = useMemo(() => (cos ?? []).length ? GROUPS.map((g) => ({
    ...g,
    rows: g.rows.map((r) => {
      const cells = cos!.map((co) => r.get(co));
      const nums = cells.map((c) => c.num).filter(Boolean) as NonNullable<Cell["num"]>[];
      const sameUnit = nums.length > 0 && nums.every((x) => x.cur === nums[0].cur);
      const comparable = sameUnit && nums.length >= 2 && nums.every((x) => x.period === nums[0].period);
      const max = sameUnit ? Math.max(...nums.map((x) => Math.abs(x.v))) : 0;
      const top = comparable ? Math.max(...nums.map((x) => x.v)) : null;
      const same = cells.every((c) => c.key === cells[0].key);
      return { ...r, cells, bars: r.bars && sameUnit && cells.length > 1, max, top, same, unitNote: r.bars && nums.length > 1 && !sameUnit };
    }),
  })) : [], [cos]);

  return (
    <div className="wrap pb-10 pt-8 sm:pt-12">
      <h1 className="display text-[2.3rem] text-ink sm:text-[3.2rem]">Compare companies</h1>
      <p className="measure mt-3 text-[16px] text-ink-2">Up to three side by side. Tap any value for its source. Figures are each company's latest filed accounts, exactly as filed; bars only show their relative size.</p>

      {/* The companies: filled slots, and one search in the first empty slot. */}
      <div className="mt-7 grid grid-cols-1 gap-2.5 sm:grid-cols-3">
        {cos?.map((co) => <Slot key={co.o} co={co} onRemove={() => update(orgs.filter((x) => x !== co.o))} />)}
        {!cos && orgs.map((o) => <div key={o} className="h-[5.2rem] animate-pulse rounded-2xl bg-paper-2" />)}
        {Array.from({ length: MAX_PICK - orgs.length }, (_, i) => i === 0 ? (
          <div key="add" className="min-w-0 rounded-2xl border border-dashed border-rule bg-card/50 p-2">
            <InlineSearch size="md" exclude={orgs} placeholder="Add a company" onPick={(r) => update([...orgs, r.o])} />
          </div>
        ) : (
          <div key={`e${i}`} className="hidden items-center justify-center gap-1.5 rounded-2xl border border-dashed border-rule text-[13.5px] text-muted sm:flex">
            <Plus className="size-4" aria-hidden />Add a company
          </div>
        ))}
      </div>

      {orgs.length === 0 && (
        <p className="mt-6 text-[15px] text-muted">Search above, or open any company and press Compare with. <Link to="/compare?o=938702675,999296432" className="text-brand underline underline-offset-2">Try AF Gruppen and Entra</Link>.</p>
      )}

      {cos && n > 0 && (
        <div className="mt-6">
          <div className="min-w-0">
            {n > 1 && (
              <label className="mb-3 inline-flex cursor-pointer items-center gap-2.5 text-[14px] text-ink-2">
                <span className="relative inline-flex h-5 w-9 items-center">
                  <input type="checkbox" className="peer sr-only" checked={diffOnly} onChange={(e) => setDiffOnly(e.target.checked)} />
                  <span className="absolute inset-0 rounded-full bg-paper-2 ring-1 ring-rule transition peer-checked:bg-brand peer-focus-visible:outline-2 peer-focus-visible:outline-brand" />
                  <span className="absolute left-0.5 size-4 rounded-full bg-card shadow transition peer-checked:translate-x-4" />
                </span>
                Show only differences
              </label>
            )}
            <div className="rounded-[1.3rem] bg-card ring-1 ring-rule">
              {/* Sticky company names, so long tables stay readable. */}
              <div className="sticky top-[4.6rem] z-10 grid rounded-t-[1.3rem] border-b border-rule bg-card/95 px-1.5 py-2.5 backdrop-blur sm:grid-cols-[11rem_1fr] sm:px-2">
                <span className="hidden px-3 text-[12px] text-muted sm:block">Company</span>
                <div className="grid" style={{ gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}>
                  {cos.map((co) => <Link key={co.o} to={`/c/${co.o}`} className="min-w-0 px-2.5 text-[13.5px] font-semibold leading-snug text-ink hover:text-brand sm:px-3 sm:text-[14.5px]">{displayName(co.n)}</Link>)}
                </div>
              </div>
              {table.map((g) => {
                const rows = diffOnly ? g.rows.filter((r) => !r.same) : g.rows;
                const f = g.fam ? FAM[g.fam] : null;
                const open = !closed[g.id];
                return (
                  <section key={g.id} className="border-b border-rule/70 last:border-b-0">
                    <h2>
                      <button onClick={() => setClosed({ ...closed, [g.id]: open })} aria-expanded={open}
                        className="flex w-full items-center gap-2 px-3 py-3 text-left sm:px-5">
                        <span className={cn("inline-flex rounded-full px-2.5 py-0.5 text-[12.5px] font-semibold", f ? cn(f.bg, f.ink) : "bg-paper-2 text-ink-2")}>{g.title}</span>
                        <span className="text-[12px] text-muted">{diffOnly ? `${rows.length} of ${g.rows.length} differ` : plural(rows.length, "row")}</span>
                        <ChevronDown className={cn("ml-auto size-4 text-muted transition", !open && "-rotate-90")} aria-hidden />
                      </button>
                    </h2>
                    {open && rows.map((r) => (
                      <div key={r.name} className="grid border-t border-rule/50 px-1.5 pb-1 sm:grid-cols-[11rem_1fr] sm:px-2">
                        <div className="px-2.5 pt-2.5 text-[12.5px] text-muted sm:px-3 sm:pb-2.5 sm:text-[13.5px]">
                          {r.name}{r.unitNote && <span className="block text-[11px]">Different currencies: not drawn to scale</span>}
                        </div>
                        <div className="grid" style={{ gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}>
                          {r.cells.map((cell, i) => (
                            <CellView key={cos[i].o} co={cos[i]} cell={cell}
                              bar={r.bars && cell.num ? <Bar v={cell.num.v} max={r.max} top={r.top != null && cell.num.v === r.top} /> : undefined} />
                          ))}
                        </div>
                      </div>
                    ))}
                    {open && diffOnly && rows.length === 0 && <p className="px-5 pb-3 text-[13px] text-muted">All the same here.</p>}
                  </section>
                );
              })}
            </div>
            <p className="mt-3 text-[12.5px] text-muted">"Highest" appears only when the figures share a currency, a year and an account type. Empty cells show what Kilde found instead: not found, not proven or not checked.</p>
          </div>
        </div>
      )}
    </div>
  );
}
