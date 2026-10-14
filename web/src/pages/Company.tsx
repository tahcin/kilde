import { motion, useReducedMotion } from "motion/react";
import { ArrowLeft, ArrowUpRight, Check, ChevronDown, Copy } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { AskPanel } from "../components/Ask";
import { SourceChip, SourcePop } from "../components/Source";
import { FactRow, render } from "../components/Value";
import { FIN, accounts, anyOf, latest, one, useCompany, type Claim, type Company } from "../lib/data";
import { LEGAL_FORM, STATE, amount, moneyShort, cn, date, dateTime, displayName, employees, industry, label, orgFmt, plural, titleCase, toneSoft } from "../lib/format";
import { useTitle } from "../lib/title";
import { CompareToggle } from "./Home";

const ease = [0.16, 1, 0.3, 1] as const;

type Group = { id: string; title: string; note: string; fields: string[]; sortByDate?: boolean };
const GROUPS: Group[] = [
  { id: "people", title: "Who runs it", note: "Registered roles in the Brønnøysund register.",
    fields: ["ceo", "board_chair", "board_deputy_chair", "board_member", "board_deputy_member", "board_observer", "owner", "partner", "general_partner",
      "business_manager", "contact_person", "norwegian_representative", "other_role", "auditor", "accountant", "signature_rule"] },
  { id: "online", title: "Website and social profiles", note: "Only from a website proven to belong to this company.",
    fields: ["official_website", "website_title", "website_description", "site_language", "social_profile", "site_contact_email", "site_contact_phone",
      "structured_data_organization", "logo", "related_website"] },
  { id: "hiring", title: "Hiring", note: "Its own careers page, and active ads in NAV's public job feed.", fields: ["hiring_signal", "active_job_count", "job_ad"] },
  { id: "news", title: "News and updates", note: "Dated posts on its verified website, and changes recorded by the register in the last 12 months.",
    fields: ["dated_news", "registry_change", "site_page_updated"], sortByDate: true },
  { id: "records", title: "Public records", note: "Contracts, research funding, state aid, inspections and approvals filed under its organisation number.",
    fields: ["public_contract_award", "procurement_notice", "research_project", "state_aid_award", "food_inspection", "approval_sentral_godkjenning", "financial_licence"], sortByDate: true },
  { id: "group", title: "Group", note: "Parent and subsidiaries from the register's group structure and the LEI register.",
    fields: ["part_of_group", "group_parent", "group_ultimate_parent", "lei_direct_parent", "lei_ultimate_parent", "group_subsidiary"] },
  { id: "workplaces", title: "Workplaces", note: "Registered sub-units: the places it operates from.", fields: ["registered_workplace"] },
  { id: "registration", title: "Registration", note: "The company's record in the Brønnøysund register, the LEI register and Wikidata.",
    fields: ["legal_name", "former_names", "legal_form", "legal_status", "incorporation_date", "registration_date", "business_register_date", "articles_date",
      "deleted_date", "industry", "secondary_industry", "institutional_sector", "statutory_purpose", "registered_activity", "share_capital",
      "employees_registered", "employees_reported", "register_memberships", "business_address", "postal_address", "municipality", "headquarters",
      "registry_website", "registry_email", "registry_phone", "registry_mobile", "lei", "stock_listing", "ticker", "isin", "wikidata_item", "inception_date"] },
];
const ACCOUNT_FIELDS = [...FIN, "accounts_audited", "latest_filed_accounts_year", "audit_opt_out_date", "annual_report_pdf"];
const PLACED = new Set([...GROUPS.flatMap((g) => g.fields), ...ACCOUNT_FIELDS, "official_website_url"]);

function Section({ id, title, note, children, count }: { id: string; title: string; note?: string; children: ReactNode; count?: number }) {
  return (
    <section id={id} className="surface scroll-mt-24 rounded-[1.3rem] px-4 pb-2 pt-5 sm:px-6" aria-labelledby={`${id}-h`}>
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h2 id={`${id}-h`} className="text-[19px] font-semibold tracking-[-0.02em] text-ink">{title}</h2>
        {count != null && <span className="tnum text-[12.5px] text-muted">{plural(count, "fact")}</span>}
      </div>
      {note && <p className="-mt-1 mb-2 text-[13px] text-muted">{note}</p>}
      {children}
    </section>
  );
}

/** Rows for a group of fields: long runs of one field show the first few, with the rest one click away. */
function Rows({ co, claims }: { co: Company; claims: Claim[] }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const byField = new Map<string, Claim[]>();
  for (const c of claims) byField.set(c.f, [...(byField.get(c.f) ?? []), c]);
  const out: ReactNode[] = [];
  for (const [f, cs] of byField) {
    const cut = cs.length > 8 && !open[f] ? 5 : cs.length;
    cs.slice(0, cut).forEach((c) => out.push(<FactRow key={c.i} co={co} claim={c} name={c.a === "available" && (c.f === "registry_change" || c.f === "dated_news" || c.f === "site_page_updated") ? <DateName c={c} /> : undefined} />));
    if (cut < cs.length) out.push(
      <div key={`${f}-more`} className="border-t border-rule/70 py-2.5">
        <button onClick={() => setOpen({ ...open, [f]: true })} className="flex items-center gap-1.5 text-[13.5px] font-medium text-brand hover:underline">
          <ChevronDown className="size-4" aria-hidden />Show all {cs.length} ({label(f).toLowerCase()})
        </button>
      </div>);
  }
  return <div>{out}</div>;
}

function DateName({ c }: { c: Claim }) {
  return <><span className="block text-ink-2">{c.d ? date(c.d) : "Undated"}</span><span className="text-[12px]">{label(c.f)}</span></>;
}

function FinTable({ co, kind }: { co: Company; kind: "company" | "group" }) {
  const rows = accounts(co, kind);
  if (!rows.length) return null;
  const years = [...new Set(rows.map((c) => String(c.v.year)))].sort().reverse();
  const per = Object.fromEntries(years.map((y) => [y, rows.find((c) => String(c.v.year) === y)!.v]));
  const currencies = [...new Set(rows.map((c) => c.v.currency).filter(Boolean))];
  const calendar = years.every((y) => !per[y].period_start || (/-01-01$/.test(per[y].period_start) && /-12-31$/.test(per[y].period_end ?? "")));
  const fields = FIN.filter((f) => rows.some((c) => c.f === f));
  const audited = co.c.filter((c) => c.f === "accounts_audited" && c.a === "available" && (c.v?.account_type ?? "company") === kind);
  return (
    <div className="mb-4 mt-2">
      <p className="mb-2 text-[13px] text-muted">
        <span className="font-medium text-ink-2">{kind === "group" ? "Group accounts" : "Company accounts"}</span>, in {currencies.join(", ") || "the currency filed"}, exactly as filed with the Register of Company Accounts{calendar ? ", calendar years" : ""}.
      </p>
      <div className="-mx-4 overflow-x-auto px-4 scroll-thin sm:mx-0 sm:px-0">
        <table className="w-full min-w-[30rem] border-separate border-spacing-0 text-[14px]">
          <thead>
            <tr>
              <th scope="col" className="sticky left-0 bg-card py-2 pr-3 text-left text-[12.5px] font-medium text-muted"><span className="sr-only">Figure</span></th>
              {years.map((y) => (
                <th key={y} scope="col" className="py-2 pl-3 text-right text-[13px] font-semibold text-ink">
                  {y}{!calendar && per[y].period_end && <span className="block text-[11px] font-normal text-muted">{date(per[y].period_start)} to {date(per[y].period_end)}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {fields.map((f) => (
              <tr key={f} className="group">
                <th scope="row" className={cn("sticky left-0 border-t border-rule/70 bg-card py-2.5 pr-3 text-left text-[13.5px] font-normal text-ink-2", f === "total_assets" && "border-t-rule")}>{label(f)}</th>
                {years.map((y) => {
                  const c = rows.find((x) => x.f === f && String(x.v.year) === y);
                  return (
                    <td key={y} className="border-t border-rule/70 py-1.5 pl-3 text-right">
                      {c ? (
                        <SourcePop co={co} claim={c} title={`${label(f)} ${y}: source`}
                          className={cn("tnum rounded-md px-1.5 py-1 transition hover:bg-wash hover:text-brand data-[state=open]:bg-wash", c.v.amount < 0 ? "text-bad" : "text-ink")}>
                          {amount(c.v.amount)}
                        </SourcePop>
                      ) : <span className="text-[12px] text-muted">Not filed</span>}
                    </td>
                  );
                })}
              </tr>
            ))}
            {audited.length > 0 && (
              <tr>
                <th scope="row" className="sticky left-0 border-t border-rule/70 bg-card py-2.5 pr-3 text-left text-[13.5px] font-normal text-ink-2">Audited</th>
                {years.map((y) => {
                  const c = audited.find((x) => String(x.v.year) === y);
                  return <td key={y} className="border-t border-rule/70 py-1.5 pl-3 text-right">{c ? <SourcePop co={co} claim={c} className="rounded-md px-1.5 py-1 hover:bg-wash hover:text-brand">{c.v.audited ? "Yes" : "No"}</SourcePop> : <span className="text-[12px] text-muted">Not filed</span>}</td>;
                })}
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[12px] text-muted">Tap any figure for its source.</p>
    </div>
  );
}

function Accounts({ co }: { co: Company }) {
  const fin = co.c.filter((c) => ACCOUNT_FIELDS.includes(c.f));
  const tables = accounts(co, "company").length + accounts(co, "group").length > 0;
  const rest = fin.filter((c) => !((FIN as readonly string[]).includes(c.f) || c.f === "accounts_audited") || c.a !== "available");
  // When figures exist, a not-found row for a single figure adds nothing; keep only the other entries.
  const extra = tables ? rest.filter((c) => !(FIN as readonly string[]).includes(c.f)) : rest;
  if (!fin.length) return null;
  return (
    <Section id="accounts" title="Accounts" note="Filed annual accounts for the last three years, from the Register of Company Accounts." count={fin.filter((c) => c.a === "available").length}>
      <FinTable co={co} kind="company" />
      <FinTable co={co} kind="group" />
      {extra.length > 0 && <Rows co={co} claims={extra} />}
    </Section>
  );
}

function Tile({ co, claim, name, children, sub }: { co: Company; claim: Claim | null; name: string; children: ReactNode; sub?: string }) {
  const body = (
    <>
      <span className="block text-[12.5px] text-muted">{name}</span>
      <span className="mt-1.5 block break-words text-[16px] font-semibold leading-tight tracking-[-0.02em] text-ink sm:text-[19px]">{children}</span>
      {sub && <span className="mt-1 block text-[12px] leading-snug text-muted">{sub}</span>}
    </>
  );
  const cls = "surface block min-w-0 rounded-2xl px-4 py-3.5 text-left";
  return claim ? <SourcePop co={co} claim={claim} title={`${name}: source`} className={cn(cls, "transition hover:border-brand/40 data-[state=open]:border-brand/50")}>{body}</SourcePop> : <div className={cls}>{body}</div>;
}

function KeyFigures({ co }: { co: Company }) {
  const rev = latest(co, "revenue"), res = latest(co, "annual_result");
  const emp = anyOf(co, "employees_registered")[0] ?? null;
  const web = anyOf(co, "official_website")[0] ?? null;
  const money = (c: Claim | null) => (c ? <span title={`${c.v.currency} ${amount(c.v.amount)}`}>{moneyShort(c.v.currency, c.v.amount)}</span> : "Not filed");
  const sub = (c: Claim | null) => (c ? `${c.v.year}, ${c.v.account_type === "group" ? "group" : "company"} accounts` : undefined);
  return (
    <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
      <Tile co={co} claim={rev} name="Revenue" sub={sub(rev)}>{money(rev)}</Tile>
      <Tile co={co} claim={res} name="Annual result" sub={sub(res)}>{money(res)}</Tile>
      <Tile co={co} claim={emp} name="Employees"
        sub={emp?.a === "available" ? (emp.d ? `Registered, as of ${date(emp.d)}` : "Registered") : emp?.n ? "Employer register (Aa-registeret)" : undefined}>
        {emp?.a === "available" ? employees(emp.v) : emp?.n ? "None registered" : "Not found"}
      </Tile>
      <Tile co={co} claim={web} name="Website" sub={web?.a === "available" ? "Verified" : web ? STATE[web.a].word : undefined}>
        {web?.a === "available" ? web.v : <span className="text-muted">None verified</span>}
      </Tile>
    </div>
  );
}

function Summary({ co }: { co: Company }) {
  const numbers = new Map<string, number>();
  const num = (id: string) => { if (!numbers.has(id)) numbers.set(id, numbers.size + 1); return numbers.get(id)!; };
  return (
    <section className="surface rounded-[1.3rem] px-5 py-5 sm:px-6" aria-labelledby="sum-h">
      <h2 id="sum-h" className="text-[19px] font-semibold tracking-[-0.02em] text-ink">In short</h2>
      <div className="mt-3 space-y-2.5 text-[15.5px] leading-[1.7] text-ink">
        {co.sum.map(([text, ids], i) => {
          const claims = ids.map((id) => co.byId[id]).filter(Boolean);
          return (
            <p key={i}>
              {text}
              {claims.slice(0, 3).map((c) => (
                <SourcePop key={c.i} co={co} claim={c} title={`Source: ${label(c.f)}`}
                  className="ml-1 inline-grid h-[18px] min-w-[18px] -translate-y-[1px] place-items-center rounded-[5px] bg-pop-soft px-1 align-middle text-[10.5px] font-semibold text-ink ring-1 ring-pop/50 transition hover:bg-pop data-[state=open]:bg-pop">
                  {num(c.i)}
                </SourcePop>
              ))}
              {claims.length > 3 && <span className="ml-1 align-middle text-[11.5px] text-muted">+{claims.length - 3} more below</span>}
            </p>
          );
        })}
      </div>
      {co.unk.length > 0 && (
        <div className="mt-5 rounded-xl bg-paper px-4 py-3">
          <h3 className="text-[13.5px] font-semibold text-ink">What Kilde could not find</h3>
          <ul className="mt-1.5 space-y-1.5 text-[13.5px] leading-snug text-ink-2">
            {co.unk.map((u, i) => <li key={i} className="relative pl-3.5 before:absolute before:left-0 before:top-[0.55em] before:size-1.5 before:rounded-full before:bg-none/50">{u}</li>)}
          </ul>
        </div>
      )}
    </section>
  );
}

function CopyOrg({ o }: { o: string }) {
  const [done, setDone] = useState(false);
  return (
    <button onClick={() => { void navigator.clipboard?.writeText(o).then(() => { setDone(true); setTimeout(() => setDone(false), 1600); }); }}
      className="flex h-8 items-center gap-1.5 rounded-full border border-rule bg-card px-3 font-mono text-[12.5px] text-ink-2 transition hover:border-brand/40 hover:text-brand"
      aria-label={`Copy organisation number ${o}`}>
      Org. no. {orgFmt(o)}{done ? <Check className="size-3.5 text-ok" aria-hidden /> : <Copy className="size-3.5 opacity-60" aria-hidden />}
    </button>
  );
}

function Header({ co }: { co: Company }) {
  const lf = one(co, "legal_form"), mun = one(co, "municipality"), ind = one(co, "industry"), founded = one(co, "incorporation_date") ?? one(co, "registration_date");
  const status = one(co, "legal_status")?.v ?? {};
  const warn = status.konkurs ? "Bankrupt" : status.underTvangsavviklingEllerTvangsopplosning ? "Being forcibly dissolved" : status.underAvvikling ? "Being wound up" : "";
  const line = [lf && (LEGAL_FORM[lf.v.code] ?? lf.v.name), mun && `in ${titleCase(mun.v.name)}`].filter(Boolean).join(" ");
  const reduce = useReducedMotion();
  return (
    <motion.header initial={reduce ? false : { opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.8, ease }}>
      <Link to="/" className="inline-flex items-center gap-1 text-[13.5px] text-muted transition hover:text-ink"><ArrowLeft className="size-3.5" aria-hidden />All companies</Link>
      <h1 className="display mt-4 text-[2.3rem] text-ink sm:text-[3.2rem]">{displayName(co.n)}</h1>
      <p className="mt-3 text-[16.5px] text-ink-2">
        {line}{ind && <> · {industry(ind.v.code, ind.v.name)}</>}{founded && <> · since {founded.v.slice(0, 4)}</>}
      </p>
      <div className="mt-5 flex flex-wrap items-center gap-2">
        {warn && <span className={cn("rounded-full px-3 py-1 text-[12.5px] font-semibold", toneSoft.bad)}>{warn}</span>}
        <CopyOrg o={co.o} />
        <CompareToggle o={co.o} />
        <a href={`https://virksomhet.brreg.no/oppslag/enheter/${co.o}`} target="_blank" rel="noopener noreferrer"
          className="flex h-8 items-center gap-1 rounded-full border border-rule bg-card px-3 text-[12.5px] font-medium text-ink-2 transition hover:border-brand/40 hover:text-brand">
          Brønnøysund record<ArrowUpRight className="size-3.5" aria-hidden />
        </a>
      </div>
    </motion.header>
  );
}

function OnThisPage({ items }: { items: [string, string][] }) {
  return (
    <nav aria-label="On this page" className="hidden px-1 lg:block">
      <p className="text-[12.5px] font-medium text-muted">On this page</p>
      <ul className="mt-2 space-y-1 text-[13.5px]">
        {items.map(([id, t]) => <li key={id}><a href={`#${id}`} className="text-ink-2 transition hover:text-brand">{t}</a></li>)}
      </ul>
    </nav>
  );
}

export const COMPANY_SUGGESTIONS = ["What does it do?", "Who runs it?", "How did it do in its latest accounts?", "Is it hiring?", "What has changed recently?", "Hvem eier selskapet?"];

export default function CompanyPage() {
  const { org = "" } = useParams();
  const { data: co, error } = useCompany(org);
  useTitle(co ? displayName(co.n) : null);
  const groups = useMemo(() => {
    if (!co) return [];
    const gs = GROUPS.map((g) => {
      let cs = co.c.filter((c) => g.fields.includes(c.f));
      // Not-found rows only where nothing of that field was found.
      cs = cs.filter((c) => c.a === "available" || !cs.some((x) => x.f === c.f && x.a === "available"));
      cs.sort((a, b) => g.fields.indexOf(a.f) - g.fields.indexOf(b.f) || (g.sortByDate ? (b.d ?? "").localeCompare(a.d ?? "") : 0));
      return { ...g, claims: cs };
    }).filter((g) => g.claims.length);
    const other = co.c.filter((c) => !PLACED.has(c.f));
    if (other.length) gs.push({ id: "other", title: "Other facts", note: "", fields: [], claims: other });
    return gs;
  }, [co]);

  if (error) return (
    <div className="mx-auto max-w-2xl px-4 py-24 text-center">
      <h1 className="display text-[2.2rem]">No profile for {orgFmt(org)}</h1>
      <p className="mt-3 text-ink-2">Kilde has published profiles for the companies in its sample runs only. Try searching by name.</p>
      <Link to="/" className="mt-6 inline-flex rounded-full bg-brand px-5 py-2.5 text-white">Search companies</Link>
    </div>
  );
  if (!co) return <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6"><div className="h-14 w-2/3 animate-pulse rounded-xl bg-paper-2" /><div className="mt-6 h-40 animate-pulse rounded-2xl bg-paper-2/60" /></div>;

  const toc: [string, string][] = [["summary", "In short"], ["people", "Who runs it"], ["accounts", "Accounts"], ...groups.filter((g) => g.id !== "people").map((g) => [g.id, g.title] as [string, string]), ["about", "About this profile"]];
  const people = groups.find((g) => g.id === "people");
  return (
    <div className="mx-auto max-w-6xl px-4 pb-10 pt-8 sm:px-6 sm:pt-12">
      <Header co={co} />
      <div className="mt-8"><KeyFigures co={co} /></div>
      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_23rem] lg:gap-8">
        <div id="summary" className="min-w-0 scroll-mt-24 lg:col-start-1"><Summary co={co} /></div>
        <aside className="min-w-0 lg:col-start-2 lg:row-span-2 lg:row-start-1">
          <div className="space-y-6 lg:sticky lg:top-24">
            <AskPanel companies={[co]} suggestions={COMPANY_SUGGESTIONS} />
            <OnThisPage items={toc} />
          </div>
        </aside>
        <div className="min-w-0 space-y-6 lg:col-start-1">
          {people && <Section id="people" title={people.title} note={people.note}><Rows co={co} claims={people.claims} /></Section>}
          <Accounts co={co} />
          {groups.filter((g) => g.id !== "people").map((g) => (
            <Section key={g.id} id={g.id} title={g.title} note={g.note} count={g.claims.filter((c) => c.a === "available").length}>
              <Rows co={co} claims={g.claims} />
            </Section>
          ))}
          <section id="about" className="scroll-mt-24 rounded-[1.3rem] border border-dashed border-rule px-5 py-5 text-[13.5px] leading-relaxed text-muted sm:px-6">
            <h2 className="text-[15px] font-semibold text-ink">About this profile</h2>
            <p className="mt-1.5">
              Built by Kilde on {dateTime(co.run.at)}{co.run.requests ? ` with ${plural(co.run.requests, "web request")}` : ""}, code version {co.run.commit}.
              {" "}{plural(co.c.filter((c) => c.a === "available").length, "fact")} found and {plural(co.c.filter((c) => c.a !== "available").length, "check")} that found nothing or could not be proven.
              No language model was used to build it.
            </p>
            <p className="mt-2 flex flex-wrap gap-x-2">Website facts come only from a site Kilde proved is this company's:{" "}
              {(() => { const w = one(co, "official_website"); return w ? <span className="inline-flex items-center gap-2 text-ink-2">{render("official_website", w.v)} <SourceChip co={co} claim={w} /></span> : "none was proven for this company."; })()}
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
