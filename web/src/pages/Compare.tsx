import { X } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { AskPanel } from "../components/Ask";
import { InlineSearch } from "../components/Search";
import { SourcePop } from "../components/Source";
import { newsParts, render, socialParts } from "../components/Value";
import { MAX_PICK, useCompare } from "../lib/compare";
import { anyOf, avail, latest, one, useCompanies, type Claim, type Company } from "../lib/data";
import { LEGAL_FORM, STATE, amount, cn, date, displayName, employees, industry, plural, titleCase } from "../lib/format";
import { useTitle } from "../lib/title";

type Cell = { claim: Claim | null; body: ReactNode } | null;
type RowDef = { name: string; get: (co: Company) => Cell };

const missing = (co: Company, f: string, word = "Not found"): Cell => {
  const c = anyOf(co, f).find((x) => x.a !== "available") ?? null;
  return { claim: c && c.e.length ? c : null, body: <span className="text-muted">{c ? STATE[c.a].word : word}</span> };
};
const fact = (f: string, show?: (c: Claim) => ReactNode) => (co: Company): Cell => {
  const c = one(co, f);
  return c ? { claim: c, body: show ? show(c) : render(f, c.v) } : missing(co, f);
};
const money = (f: string) => (co: Company): Cell => {
  const c = latest(co, f);
  if (!c) return { claim: null, body: <span className="text-muted">Not filed</span> };
  return { claim: c, body: <><span className={cn("tnum font-medium", c.v.amount < 0 && "text-bad")}>{c.v.currency} {amount(c.v.amount)}</span><span className="block text-[12px] text-muted">{c.v.year}{c.v.account_type === "group" ? ", group" : ""}</span></> };
};
const many = (f: string, noun: string, show: (cs: Claim[]) => ReactNode) => (co: Company): Cell => {
  const cs = avail(co, f);
  return cs.length ? { claim: cs[0], body: <>{show(cs)}</> } : missing(co, f, `No ${noun}`);
};

const SECTIONS: [string, RowDef[]][] = [
  ["Basics", [
    { name: "Legal form", get: fact("legal_form", (c) => LEGAL_FORM[c.v.code] ?? c.v.name) },
    { name: "Municipality", get: fact("municipality", (c) => titleCase(c.v.name)) },
    { name: "Industry", get: fact("industry", (c) => <>{industry(c.v.code, c.v.name)}<span className="block text-[12px] text-muted">{c.v.code}</span></>) },
    { name: "Founded", get: fact("incorporation_date", (c) => date(c.v)) },
    { name: "Employees", get: fact("employees_registered", (c) => employees(c.v)) },
  ]],
  ["Latest accounts", [
    { name: "Revenue", get: money("revenue") },
    { name: "Operating result", get: money("operating_result") },
    { name: "Annual result", get: money("annual_result") },
    { name: "Equity", get: money("equity") },
    { name: "Total assets", get: money("total_assets") },
  ]],
  ["People", [
    { name: "General manager", get: fact("ceo") },
    { name: "Board chair", get: fact("board_chair") },
    { name: "Auditor", get: fact("auditor") },
  ]],
  ["Online", [
    { name: "Verified website", get: fact("official_website") },
    { name: "Social profiles", get: many("social_profile", "profiles found", (cs) => cs.map((c) => socialParts(c.v).platform).join(", ")) },
    { name: "Careers page", get: fact("hiring_signal") },
    { name: "Job ads on NAV", get: many("job_ad", "active ads", (cs) => plural(cs.length, "active ad")) },
    { name: "News posts", get: many("dated_news", "dated news", (cs) => {
      const latestPost = [...cs].sort((a, b) => (b.d ?? "").localeCompare(a.d ?? ""))[0];
      return <>{plural(cs.length, "post")}<span className="block truncate text-[12px] text-muted">Latest: {newsParts(latestPost.v).title}</span></>;
    }) },
  ]],
  ["Public records", [
    { name: "Public contracts won", get: many("public_contract_award", "contracts found", (cs) => plural(cs.length, "contract")) },
    { name: "Research projects", get: many("research_project", "projects found", (cs) => plural(cs.length, "project")) },
    { name: "State aid awards", get: many("state_aid_award", "awards found", (cs) => plural(cs.length, "award")) },
  ]],
];

function CellView({ co, cell }: { co: Company; cell: Cell }) {
  if (!cell) return null;
  const inner = <span className="block min-w-0 break-words text-[14px] leading-snug text-ink [&_a]:pointer-events-none [&_a]:no-underline">{cell.body}</span>;
  if (!cell.claim) return <div className="px-3 py-2.5">{inner}</div>;
  return (
    <SourcePop co={co} claim={cell.claim} className="group flex w-full flex-col items-start rounded-xl px-3 py-2.5 text-left transition hover:bg-wash/70 data-[state=open]:bg-wash">
      {inner}
      <span className="mt-1 flex items-center gap-1 text-[11px] text-muted opacity-70 group-hover:opacity-100"><span className="size-1.5 rounded-full bg-pop" aria-hidden />Source</span>
    </SourcePop>
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
  const cols = orgs.length;

  return (
    <div className="mx-auto max-w-6xl px-4 pb-10 pt-8 sm:px-6 sm:pt-12">
      <h1 className="display text-[2.3rem] text-ink sm:text-[3.2rem]">Compare companies</h1>
      <p className="mt-3 max-w-2xl text-[16px] text-ink-2">Up to three side by side. Every value opens its source; figures are each company's latest filed accounts, with the year shown.</p>

      <div className="mt-7 flex flex-wrap items-center gap-2">
        {orgs.map((o) => {
          const co = cos?.find((c) => c.o === o);
          return (
            <span key={o} className="flex items-center gap-1 rounded-full bg-wash py-1.5 pl-3.5 pr-1.5 text-[14px] font-medium text-brand">
              {co ? displayName(co.n) : o}
              <button onClick={() => update(orgs.filter((x) => x !== o))} className="grid size-6 place-items-center rounded-full hover:bg-brand/10" aria-label={`Remove ${co ? displayName(co.n) : o}`}><X className="size-3.5" /></button>
            </span>
          );
        })}
        {orgs.length < MAX_PICK && (
          <div className="w-full sm:w-80">
            <InlineSearch size="md" exclude={orgs} placeholder={orgs.length ? "Add a company" : "Pick a company to start"} onPick={(r) => update([...orgs, r.o])} />
          </div>
        )}
      </div>

      {orgs.length === 0 && (
        <p className="mt-10 text-[15px] text-muted">Search above, or press Compare on any company. <Link to="/c/938702675" className="text-brand underline underline-offset-2">Try AF Gruppen</Link> to start.</p>
      )}

      {cos && cos.length > 0 && (
        <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_23rem] lg:items-start">
          <div className="surface min-w-0 overflow-hidden rounded-[1.3rem]">
            <div className="grid border-b border-rule bg-paper/60 px-2 py-3 lg:grid-cols-[9.5rem_1fr]" >
              <span className="hidden px-3 text-[12.5px] text-muted lg:block">Company</span>
              <div className="grid gap-1" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
                {cos.map((co) => (
                  <Link key={co.o} to={`/c/${co.o}`} className="min-w-0 rounded-lg px-3 py-1 text-[15px] font-semibold leading-snug text-ink hover:text-brand">{displayName(co.n)}</Link>
                ))}
              </div>
            </div>
            {SECTIONS.map(([title, rows]) => (
              <div key={title} className="border-b border-rule/70 px-2 pb-2 pt-4 last:border-b-0">
                <h2 className="px-3 pb-1 text-[13px] font-semibold text-brand">{title}</h2>
                {rows.map((r) => (
                  <div key={r.name} className="grid border-t border-rule/50 first-of-type:border-t-0 lg:grid-cols-[9.5rem_1fr]">
                    <div className="px-3 pt-2.5 text-[12.5px] text-muted lg:pb-2.5 lg:text-[13.5px]">{r.name}</div>
                    <div className="grid gap-1" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
                      {cos.map((co) => <CellView key={co.o} co={co} cell={r.get(co)} />)}
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
          <div className="lg:sticky lg:top-24">
            <AskPanel companies={cos} title={cos.length > 1 ? "Ask about these companies" : undefined}
              suggestions={cos.length > 1 ? ["Which is larger by revenue?", "Compare who runs them", "Which ones are hiring?", "How do their latest results compare?", "Hvilke har verifisert nettside?"]
                : ["What does it do?", "Who runs it?", "Is it hiring?"]} />
          </div>
        </div>
      )}
    </div>
  );
}
