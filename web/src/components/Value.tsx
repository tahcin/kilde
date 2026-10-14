// How each kind of claim value reads on the page. Every value is shown as Kilde claimed it; only its layout changes.
import { ArrowUpRight } from "lucide-react";
import type { ReactNode } from "react";
import type { Claim, Company } from "../lib/data";
import { LANG, LEGAL_FORM, label, PLATFORM, REGS, STATE, amount, bare, cap, cn, date, displayName, industry, orgFmt, phone, plural, safeUrl, signText, titleCase, toneSoft } from "../lib/format";
import { SourceChip } from "./Source";

export function Ext({ href, children, className }: { href?: string; children: ReactNode; className?: string }) {
  const url = safeUrl(href) ?? (href && /^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(\/\S*)?$/i.test(href) ? `https://${href}` : undefined);
  if (!url) return <>{children}</>;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer"
      className={cn("break-words text-brand underline decoration-brand/25 underline-offset-[3px] transition hover:decoration-brand", className)}>
      {children}<ArrowUpRight className="ml-0.5 inline size-3 -translate-y-px align-baseline" aria-hidden />
    </a>
  );
}

const Sub = ({ children }: { children: ReactNode }) => <span className="text-muted">{children}</span>;
const Note = ({ children }: { children: ReactNode }) => <span className="mt-0.5 block text-[13px] text-muted">{children}</span>;

export function socialParts(v: string): { platform: string; handle: string; url: string } {
  const host = v.split("/", 1)[0];
  return { platform: PLATFORM[host] ?? cap(host.split(".")[0]), handle: v.slice(host.length + 1), url: `https://${v}` };
}

/** Splits a dated news value "Title (2025-11-18T14:52:18+00:00)" into its title and the source's datetime. */
export function newsParts(v: string): { title: string; when: string } {
  const m = /^(.*)\s\(([^()]+)\)$/.exec(v);
  return m ? { title: m[1], when: m[2] } : { title: v, when: "" };
}

export function render(f: string, v: any): ReactNode {  // eslint-disable-line @typescript-eslint/no-explicit-any
  if (v == null) return null;
  if (typeof v === "string") {
    if (f === "social_profile") { const s = socialParts(v); return <><span className="font-medium">{s.platform}</span> <Ext href={s.url}>{s.handle}</Ext></>; }
    if (f === "dated_news") { const n = newsParts(v); return <>{n.title}{n.when && <Note>{date(n.when)}</Note>}</>; }
    if (f === "official_website" || f === "official_website_url" || f === "hiring_signal" || f === "registry_website") return <Ext href={v}>{bare(v)}</Ext>;
    if (safeUrl(v)) return <Ext href={v}>{bare(v)}</Ext>;
    if (/date/.test(f) || /^\d{4}-\d{2}-\d{2}$/.test(v)) return date(v);
    if (/phone|mobile/.test(f)) return <a className="tnum hover:text-brand" href={`tel:${v.replace(/\s/g, "")}`}>{phone(v)}</a>;
    if (/email/.test(f)) return <a className="break-all text-brand hover:underline" href={`mailto:${v}`}>{v}</a>;
    if (f === "site_language") return LANG[v.slice(0, 2).toLowerCase()] ?? v;
    if (f === "signature_rule") { const t = signText(v); return t ? <>{t}<Note>Registered as: {v}</Note></> : v; }
    if (f === "employees_registered") return cap(v);
    return v;
  }
  if (typeof v === "number") return <span className="tnum">{amount(v)}</span>;
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (Array.isArray(v)) {
    if (f === "former_names") return v.map((x, i) => (
      <span key={i} className="block">{displayName(x.navn ?? x.name ?? "")} <Sub>{date(x.fraDato)} to {date(x.tilDato)}</Sub></span>
    ));
    return v.map((x, i) => <span key={i} className="block">{render(f, x)}</span>);
  }
  if (f === "legal_form") return <>{LEGAL_FORM[v.code] ?? v.name} <Sub>{v.code}, {v.name}</Sub></>;
  if (f === "industry" || f === "secondary_industry") return <>{industry(v.code, v.name)}<Note>Registered as {v.code}, {v.name}</Note></>;
  if (f === "municipality") return <>{titleCase(v.name)} <Sub>{v.number}</Sub></>;
  if (f === "institutional_sector") return <>{v.name} <Sub>{v.code}</Sub></>;
  if (v.amount != null && v.currency != null && f === "share_capital")
    return <><span className="tnum">{v.currency} {amount(v.amount)}</span>{v.shares != null && <Note>{amount(v.shares)} shares</Note>}</>;
  if (v.amount != null && v.year) return <span className="tnum">{v.currency} {amount(v.amount)} <Sub>{v.year}, {v.account_type ?? "company"} accounts</Sub></span>;
  if (f === "legal_status") {
    const on = [v.konkurs && "Bankrupt", v.underAvvikling && "Being wound up", v.underTvangsavviklingEllerTvangsopplosning && "Being forcibly dissolved"].filter(Boolean);
    return on.length ? <span className="font-medium text-bad">{on.join(", ")}</span> : "Active: no bankruptcy, winding-up or forced dissolution registered";
  }
  if (f === "register_memberships") {
    const yes = Object.keys(v).filter((k) => v[k]).map((k) => REGS[k] ?? k);
    return <>{yes.length ? yes.join(", ") : "None"}{v.registrertIMvaregisteret === false && <Note>Not VAT registered</Note>}</>;
  }
  if (v.role_code || (v.role && v.name)) return <>{v.organisation_number ? displayName(v.name) : v.name}{v.organisation_number && <> <Sub>{orgFmt(v.organisation_number)}</Sub></>}</>;
  if (f === "job_ad") return <>{v.url ? <Ext href={v.url}>{v.title ?? "Job ad"}</Ext> : v.title ?? "Job ad"}<Note>{[v.location, v.published && `published ${date(v.published)}`, v.expires && `closes ${date(v.expires)}`].filter(Boolean).join(", ")}</Note></>;
  if (f === "registered_workplace") return <>{displayName(v.name ?? "")}<Note>{[v.address, v.industry, v.employees != null && plural(v.employees, "employee"), v.start_date && `since ${date(v.start_date)}`].filter(Boolean).join(" · ")}</Note></>;
  if (f === "group_parent" || f === "group_subsidiary" || f === "group_ultimate_parent")
    return <>{displayName(v.name ?? "")} <Sub>{orgFmt(v.organisation_number ?? "")}{v.ownership ? `, ${v.ownership} owned` : ""}{/^indirect/.test(v.relationship ?? "") ? `, ${v.relationship}` : ""}</Sub></>;
  if (f === "lei_direct_parent" || f === "lei_ultimate_parent") return <>{displayName(v.name ?? "")} <Sub>LEI {v.lei}</Sub></>;
  if (f === "lei") return <><span className="font-mono text-[13px]">{v.lei}</span><Note>{[v.status && `Entity ${String(v.status).toLowerCase()}`, v.registration_status && `registration ${String(v.registration_status).toLowerCase()}`, v.next_renewal && `renewal due ${date(v.next_renewal)}`].filter(Boolean).join(", ")}</Note></>;
  if (f === "registry_change") {
    const ch: string[] = (v.changed ?? []).filter((x: string) => !/^unspecified/i.test(x));
    return ch.length ? <>Changed: {ch.join(", ")}</> : "Registry update (the feed does not say what changed)";
  }
  if (f === "site_page_updated") return <>Page updated: <Ext href={v.url}>{bare(v.url).replace(/^[^/]+/, "") || "/"}</Ext></>;
  if (f === "related_website") return <><Ext href={v.url}>{v.domain ?? bare(v.url)}</Ext><Note>{cap(v.relationship ?? "")}; not counted as this company's own site</Note></>;
  if (f === "structured_data_organization") return <>{v.name} <Sub>schema.org {v.type}</Sub></>;
  if (f === "wikidata_item") return <Ext href={v.url}>{v.label ?? v.qid}</Ext>;
  if (f === "employees_reported") return <>{amount(v.count)}<Note>{v.point_in_time ? `As of ${date(v.point_in_time)}` : ""}</Note></>;
  if (f === "stock_listing") return <>{v.exchange}{v.ticker && <> <Sub>ticker {v.ticker}</Sub></>}</>;
  if (f === "logo") return <Ext href={v.url}>{v.file}</Ext>;
  if (f === "headquarters") return v.name;
  if (f === "accounts_audited") return v.audited ? "Yes" : "No";
  if (f === "annual_report_pdf") return <Ext href={v.url}>{v.year}</Ext>;
  if (f === "public_contract_award" || f === "procurement_notice")
    return <><Ext href={v.page_url ?? v.url}>{v.title ?? `TED notice ${v.notice}`}</Ext><Note>{[f === "public_contract_award" && v.buyer && `Buyer: ${v.buyer}`, v.date && date(v.date), v.notice_type].filter(Boolean).join(" · ")}</Note></>;
  if (f === "research_project")
    return <>{v.title ?? "Untitled project"}<Note>{[v.scheme, v.period, v.status, v.awarded_nok != null && `NOK ${amount(v.awarded_nok)} awarded`].filter(Boolean).join(" · ")}</Note></>;
  if (f === "food_inspection")
    return <>{cap(v.result ?? `Grade ${v.grade}`)}<Note>{[v.place, v.date && date(v.date), v.inspections_on_file && plural(v.inspections_on_file, "inspection") + " on file"].filter(Boolean).join(" · ")}</Note></>;
  if (f === "approval_sentral_godkjenning")
    return <>{v.status === "approved" ? "Approved" : cap(v.status ?? "")}{v.valid_to && ` until ${date(v.valid_to)}`}<Note>{(v.areas ?? []).slice(0, 4).map((a: { function?: string; area?: string; class?: string }) => `${a.function ?? ""} ${a.area ?? ""}${a.class ? ` (class ${a.class})` : ""}`.trim()).join("; ")}{(v.areas ?? []).length > 4 ? `; ${v.areas.length - 4} more` : ""}</Note></>;
  if (f === "state_aid_award")
    return <><span className="tnum">{v.currency ?? "NOK"} {v.amount}</span><Note>{[v.granting_authority && titleCase(v.granting_authority), v.purpose, v.instrument, v.date].filter(Boolean).join(" · ")}</Note></>;
  if (f === "financial_licence") return <>{v.licence ?? v.type ?? v.name ?? ""}</>;
  if (v.url) return <Ext href={v.url}>{v.title ?? bare(v.url)}</Ext>;
  if (v.code != null && v.name) return <>{v.name} <Sub>{v.code}</Sub></>;
  return Object.entries(v).filter(([, x]) => x != null && typeof x !== "object").map(([k, x]) => `${k.replace(/_/g, " ")}: ${x}`).join(", ");
}

/** A claim's value, or for anything not found, its state and the reason in words. */
export function ClaimValue({ claim }: { claim: Claim }) {
  if (claim.a !== "available") {
    const st = STATE[claim.a];
    return (
      <>
        <span className={cn("inline-flex rounded-full px-2 py-px text-[11.5px] font-semibold", toneSoft[st.tone])}>{st.word}</span>
        {claim.n && <Note>{cap(claim.n)}</Note>}
      </>
    );
  }
  return <>{render(claim.f, claim.v)}{claim.n && <Note>{claim.n}</Note>}</>;
}

/** One fact: its name, its value, and its source. */
export function FactRow({ co, claim, name }: { co: Company; claim: Claim; name?: ReactNode }) {
  return (
    <div id={`f-${co.o}-${claim.i}`} className="grid scroll-mt-28 grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 gap-y-0.5 border-t border-rule/70 py-3 first:border-t-0 sm:grid-cols-[11.5rem_minmax(0,1fr)_auto]">
      <div className="col-span-2 text-[13px] text-muted sm:col-span-1 sm:pt-px sm:text-[13.5px]">{name ?? label(claim.f)}</div>
      <div className="col-start-1 row-start-2 min-w-0 text-[15px] leading-snug text-ink sm:col-start-2 sm:row-start-1"><ClaimValue claim={claim} /></div>
      <div className="col-start-2 row-start-2 sm:col-start-3 sm:row-start-1"><SourceChip co={co} claim={claim} /></div>
    </div>
  );
}

