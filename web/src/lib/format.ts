// Plain-language names and formatting. Formatting only: no figure here is ever computed from another.
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { Row, State } from "./data";

export const cn = (...inputs: ClassValue[]) => twMerge(clsx(inputs));

export const FIELD: Record<string, string> = {
  legal_name: "Legal name", former_names: "Former names", legal_form: "Legal form", registration_date: "Registered",
  business_register_date: "In the Register of Business Enterprises", articles_date: "Articles of association",
  incorporation_date: "Founded", business_address: "Business address", postal_address: "Postal address",
  municipality: "Municipality", industry: "Industry", secondary_industry: "Secondary industry", institutional_sector: "Sector",
  statutory_purpose: "Purpose (articles)", registered_activity: "Activity (registered)", share_capital: "Share capital",
  employees_registered: "Employees", employees_reported: "Employees (Wikidata)", legal_status: "Status",
  register_memberships: "Registers", signature_rule: "Who can sign", part_of_group: "Part of a group",
  group_parent: "Parent company", group_subsidiary: "Subsidiary", group_ultimate_parent: "Ultimate parent",
  deleted_date: "Deleted", registry_email: "Email", registry_phone: "Phone", registry_mobile: "Mobile",
  registry_website: "Website (as registered)", lei: "LEI", lei_direct_parent: "LEI direct parent",
  lei_ultimate_parent: "LEI ultimate parent", financial_licence: "Financial licence", ticker: "Ticker", isin: "ISIN",
  stock_listing: "Stock listing", wikidata_item: "Wikidata", headquarters: "Headquarters", inception_date: "Inception",
  logo: "Logo", revenue: "Revenue", operating_result: "Operating result", salary_costs: "Salary costs",
  profit_before_tax: "Profit before tax", annual_result: "Annual result", total_assets: "Total assets",
  cash_and_bank: "Cash and bank", equity: "Equity", debt: "Debt", accounts_audited: "Audited",
  annual_report_pdf: "Filed annual reports", latest_filed_accounts_year: "Latest filed accounts",
  audit_opt_out_date: "Opted out of audit", ceo: "General manager", board_chair: "Board chair",
  board_deputy_chair: "Deputy chair", board_member: "Board member", board_deputy_member: "Deputy board member",
  board_observer: "Observer", auditor: "Auditor", accountant: "Accountant", contact_person: "Contact person",
  owner: "Owner", partner: "Partner", general_partner: "General partner", business_manager: "Business manager",
  norwegian_representative: "Norwegian representative", other_role: "Other role",
  registered_workplace: "Workplace", official_website: "Verified website", official_website_url: "Website address",
  related_website: "Related website", website_description: "Description", website_title: "Page title",
  site_language: "Site language", structured_data_organization: "Organisation data on the site",
  social_profile: "Social profile", site_contact_email: "Email on the website", site_contact_phone: "Phone on the website",
  public_contract_award: "Public contract won", procurement_notice: "Tender published", research_project: "Research project",
  food_inspection: "Food safety inspection", approval_sentral_godkjenning: "Approved for building work",
  state_aid_award: "State aid", job_ad: "Job ads on NAV", active_job_count: "Active job ads", hiring_signal: "Careers page",
  dated_news: "News post", registry_change: "Registry update", site_page_updated: "Website page updated",
};
export const label = (f: string) => FIELD[f] ?? cap(f.replace(/_/g, " "));

export const STATE: Record<State, { word: string; tone: Tone }> = {
  available: { word: "Found", tone: "ok" },
  not_available: { word: "Not found", tone: "none" },
  ambiguous: { word: "Not proven", tone: "amber" },
  blocked: { word: "Blocked", tone: "amber" },
  failed: { word: "Not checked", tone: "bad" },
  not_applicable: { word: "Does not apply", tone: "none" },
};
export type Tone = "ok" | "amber" | "bad" | "none" | "brand";
export const toneSoft: Record<Tone, string> = {
  ok: "bg-ok-soft text-ok", amber: "bg-amber-soft text-amber", bad: "bg-bad-soft text-bad", none: "bg-none-soft text-none",
  brand: "bg-wash text-brand",
};

export const METHOD: Record<string, string> = {
  registry_endpoint: "Read from the official register's record for this organisation number, so it cannot belong to another company.",
  orgnr_on_page: "The page prints this company's organisation number.",
  registry_website_and_name: "It is the website listed in the official register, and the site shows the company's name.",
  phone_and_name: "The site shows the phone number from the register next to the company's name, and no other company shares that name.",
  address_and_name: "The site shows the registered address with the company's name, and no other company is registered there.",
  email_and_name: "The register's email domain is this site, and the site shows the company's name.",
  legal_name_unique: "The domain is exactly the company's registered name, the site shows that name with its legal form, and no other company carries it.",
  verified_site: "Read from the company's verified website.",
  verified_site_news: "A dated post on the company's verified website.",
  verified_site_sitemap: "From the sitemap of the company's verified website.",
  linked_from_verified_site: "Linked from the company's verified website.",
  registered_as_equals_orgnr: "The GLEIF record is registered under this organisation number.",
  wikidata_p2333_equals_orgnr: "The Wikidata item carries this organisation number.",
  organisasjonsnummer_stottemottaker_equals_orgnr: "The state aid register names this organisation number as the recipient.",
  organisasjonsnummer_equals_orgnr: "The record carries this organisation number.",
  organisasjonsnr_equals_orgnr: "The record carries this organisation number.",
  organisation_number_field: "The record carries this organisation number.",
  organisation_identifier_buyer_equals_orgnr: "The EU procurement notice names this organisation number as the buyer.",
  winner_identifier_equals_orgnr: "The EU procurement notice names this organisation number.",
  orgnummer_underenhet_of_orgnr: "Filed under one of this company's registered workplaces.",
  employer_orgnr: "The job ad is filed under this organisation number.",
  nav_orgnr: "The job ad is filed under this organisation number.",
};

export const SOURCE_CLASS: Record<string, string> = {
  official_registry: "Official register", official_annual_accounts: "Official accounts register", company_owned: "Company website",
  open_register: "Open register", open_data: "Open data", public_job_feed: "Public job feed", official_register: "Official register", public_register: "Public register", previous_run: "Earlier run",
};

const HOSTS: [RegExp, string][] = [
  [/data\.brreg\.no\/regnskapsregisteret/, "Brønnøysund Register Centre: annual accounts"],
  [/data\.brreg\.no\/enhetsregisteret\/api\/(roller|enheter\/\d+\/roller)/, "Brønnøysund Register Centre: roles"],
  [/data\.brreg\.no\/enhetsregisteret\/api\/oppdateringer/, "Brønnøysund Register Centre: update feed"],
  [/data\.brreg\.no/, "Brønnøysund Register Centre"],
  [/stotte\.brreg\.no/, "State aid register (Støtteregisteret)"],
  [/api\.gleif\.org/, "GLEIF LEI register"],
  [/wikidata\.org|qlever/, "Wikidata"],
  [/nav\.no/, "NAV job ads"],
  [/finanstilsynet/, "Finanstilsynet register"],
  [/ted\.europa\.eu/, "EU procurement journal (TED)"],
  [/forskningsradet/i, "Research Council of Norway"],
  [/mattilsynet/, "Mattilsynet food inspections"],
  [/dibk\.no/, "DiBK central approval register"],
];
export function sourceName(url: string, cls: string): string {
  for (const [re, name] of HOSTS) if (re.test(url)) return name;
  if (cls === "company_owned") return `${host(url)} (company website)`;
  return host(url);
}

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
export function date(s?: string | null): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s ?? "");
  return m ? `${+m[3]} ${MON[+m[2] - 1]} ${m[1]}` : s ?? "";
}
export function dateTime(s?: string | null): string {
  const m = /T(\d{2}:\d{2})/.exec(s ?? "");
  return date(s) + (m ? `, ${m[1]} UTC` : "");
}
/** Thin-spaced digits, a true minus: 194 000 000. The figure itself is unchanged. */
export function amount(n: number | string): string {
  const x = Number(n);
  if (!Number.isFinite(x)) return String(n);
  const s = Math.abs(x).toLocaleString("en-GB", { maximumFractionDigits: 2 }).replace(/,/g, " ");
  return x < 0 ? `−${s}` : s;
}
/** A figure for tight spaces: "NOK 4.28 billion", "NOK 914 million", "NOK 258.8 million", "NOK 812 000".
 *  `rounded` says whether the label differs from the filed value; `exact` is the full filed figure. */
export function money(currency: string | undefined, n: number | string): { label: string; exact: string; rounded: boolean } {
  const x = Number(n), cur = currency ? `${currency} ` : "";
  const exact = `${cur}${amount(n)}`;
  if (!Number.isFinite(x) || Math.abs(x) < 1e6) return { label: exact, exact, rounded: false };
  // Round in millions first, so 999 999 999 reads "1 billion", not "1 000 million".
  const [unit, word, dp] = Math.abs(Number((x / 1e6).toFixed(1))) >= 1000 ? [1e9, "billion", 2] : [1e6, "million", 1];
  const v = x / unit;
  const shown = Number.isInteger(v) ? v : Number(v.toFixed(dp));
  const text = shown.toLocaleString("en-GB", { maximumFractionDigits: dp }).replace(/,/g, "\u202f").replace(/^-/, "\u2212");
  return { label: `${cur}${text} ${word}`, exact, rounded: shown * unit !== x };
}
/** The tooltip for a compact figure: the exact filed value, and "about" when the label is rounded. */
export const moneyTitle = (m: { label: string; exact: string; rounded: boolean }) => (m.rounded ? `About ${m.label}; filed as ${m.exact}` : `Filed as ${m.exact}`);
/** Kept for callers that only want the label. */
export const moneyShort = (currency: string | undefined, n: number | string) => money(currency, n).label;
export const orgFmt = (o: string) => o.replace(/^(\d{3})(\d{3})(\d{3})$/, "$1 $2 $3");
export function host(u: string): string { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } }
export const bare = (u: string) => String(u ?? "").replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");
export const safeUrl = (u?: string | null) => (u && /^https?:\/\//i.test(u) ? u : undefined);
export const plural = (n: number, w: string, ws = w + "s") => `${n.toLocaleString("en-GB")} ${n === 1 ? w : ws}`;
export function phone(p: string): string {
  let s = String(p ?? "").replace(/\s/g, ""), cc = "";
  if (/^\+47\d{8}$/.test(s)) { cc = "+47 "; s = s.slice(3); }
  if (!/^\d{8}$/.test(s)) return p;
  return cc + (/^[49]/.test(s) ? s.replace(/(\d{3})(\d{2})(\d{3})/, "$1 $2 $3") : s.replace(/(\d{2})(\d{2})(\d{2})(\d{2})/, "$1 $2 $3 $4"));
}
export const titleCase = (s: string) => String(s ?? "").toLowerCase().replace(/(^|[\s\-/(])(\p{L})/gu, (_m, a, b) => a + b.toUpperCase());

const KEEPU = new Set(["AS", "ASA", "ANS", "DA", "SA", "BA", "NUF", "ENK", "KS", "IKS", "STI", "LTD", "AB", "BRL", "ESEK", "II", "III", "IT", "TV", "TGS", "DIPS", "AF", "RSV"]);
/** "ENTRA ASA" -> "Entra ASA". Registered names are upper case; this only changes letter case for reading. */
export function displayName(n: string): string {
  if (!n || n !== n.toUpperCase()) return n;
  let first = true;
  return n.split(/([\s.\-/&]+)/).map((w) => {
    if (!w || /^[\s.\-/&]+$/.test(w)) return w;
    const u = w.toUpperCase();
    const out = KEEPU.has(u) || /\d/.test(w) || (first && w.length <= 3) || (w.length <= 2 && !/^(OG|AV|I|PÅ|DE|EN|ET|TIL)$/.test(u))
      ? u : u[0] + u.slice(1).toLowerCase();
    first = false;
    return out;
  }).join("");
}

export const LEGAL_FORM: Record<string, string> = {
  AS: "Private limited company", ASA: "Public limited company", ENK: "Sole proprietorship", ANS: "General partnership",
  DA: "Partnership with shared liability", NUF: "Norwegian branch of a foreign company", SA: "Cooperative",
  BA: "Company with limited liability", STI: "Foundation", FLI: "Association", ESEK: "Co-owned residential building",
  BRL: "Housing cooperative", KS: "Limited partnership", IKS: "Inter-municipal company", SAM: "Co-ownership",
  ORGL: "Organisation", KF: "Municipal enterprise", PK: "Pension fund", SPA: "Savings bank", PRE: "Shipping partnership",
  GFS: "Mutual insurance company", KOMM: "Municipality", STAT: "State body", FKF: "County enterprise", SF: "State enterprise",
  KIRK: "Church body", "SÆR": "Entity under special legislation", VPFO: "Securities fund", OPMV: "Unit within a public body", AAFY: "Workplace", BEDR: "Workplace",
};

const IND: Record<string, string> = {
  "00.000": "Industry not stated", "41.000": "Building construction", "43.120": "Groundwork", "43.210": "Electrical installation",
  "43.221": "Plumbing", "43.222": "Heating and ventilation", "43.320": "Carpentry", "43.340": "Painting and glazing",
  "47.110": "Grocery store", "47.810": "Car dealer", "49.410": "Road freight", "55.101": "Hotel", "56.101": "Restaurant",
  "62.010": "Software development", "62.100": "Software development", "62.020": "IT consulting", "62.200": "IT consulting",
  "64.202": "Holding company", "64.323": "Investment company", "68.100": "Buying and selling own property",
  "68.120": "Property development", "68.200": "Property rental", "68.209": "Property rental", "68.320": "Property management",
  "69.100": "Legal services", "69.201": "Accounting", "70.100": "Head office", "70.200": "Management consulting",
  "71.112": "Architecture", "71.129": "Technical consulting", "73.110": "Advertising agency", "81.210": "Cleaning",
  "85.201": "Primary school", "86.210": "General practitioner", "94.991": "Charitable foundation", "95.310": "Car repair",
  "97.001": "Housing cooperative",
};
const DIV: Record<string, string> = {
  "01": "Farming", "02": "Forestry", "03": "Fishing and aquaculture", "06": "Oil and gas", "09": "Oil and gas services",
  "10": "Food production", "11": "Drinks production", "16": "Wood products", "18": "Printing", "20": "Chemicals",
  "22": "Rubber and plastics", "23": "Building materials", "25": "Metal products", "26": "Electronics",
  "27": "Electrical equipment", "28": "Machinery", "29": "Vehicles", "30": "Ships and transport equipment", "31": "Furniture",
  "32": "Manufacturing", "33": "Machinery repair and installation", "35": "Energy", "36": "Water supply", "37": "Sewage",
  "38": "Waste and recycling", "41": "Building construction", "42": "Civil engineering", "43": "Construction trades",
  "45": "Vehicle trade and repair", "46": "Wholesale", "47": "Retail", "49": "Land transport", "50": "Shipping",
  "51": "Aviation", "52": "Warehousing and logistics", "53": "Post and courier", "55": "Accommodation",
  "56": "Restaurants and catering", "58": "Publishing", "59": "Film and music", "60": "Broadcasting", "61": "Telecoms",
  "62": "Software and IT services", "63": "Information services", "64": "Financial services", "65": "Insurance",
  "66": "Financial support services", "68": "Real estate", "69": "Legal and accounting", "70": "Management consulting",
  "71": "Architecture and engineering", "72": "Research", "73": "Advertising and market research",
  "74": "Design and other professional services", "75": "Veterinary services", "77": "Rental and leasing",
  "78": "Recruitment and staffing", "79": "Travel", "80": "Security", "81": "Building services and cleaning",
  "82": "Office and business support", "84": "Public administration", "85": "Education", "86": "Health care",
  "87": "Residential care", "88": "Social services", "90": "Arts and entertainment", "91": "Libraries and museums",
  "92": "Gambling", "93": "Sports and recreation", "94": "Associations and foundations", "95": "Repair services",
  "96": "Personal services", "97": "Housing associations",
};
/** A short English name for a registered industry code; the registered Norwegian name is shown beside it on the page. */
export function industry(code?: string, name?: string): string {
  if (!code) return "";
  return IND[code] ?? DIV[code.slice(0, 2)] ?? cap((name ?? "").toLowerCase());
}

export function employees(v: Row["e"]): string {
  if (v == null) return "";
  if (typeof v === "number") return v.toLocaleString("en-GB");
  return /fewer than 5/i.test(v) ? "Fewer than 5" : cap(v.replace(/\s*\(.*\)$/, ""));
}

export const LANG: Record<string, string> = { nb: "Norwegian (Bokmål)", nn: "Norwegian (Nynorsk)", no: "Norwegian", en: "English", sv: "Swedish", da: "Danish", de: "German" };
export const PLATFORM: Record<string, string> = {
  "linkedin.com": "LinkedIn", "facebook.com": "Facebook", "instagram.com": "Instagram", "youtube.com": "YouTube",
  "x.com": "X", "twitter.com": "X", "tiktok.com": "TikTok", "github.com": "GitHub", "pinterest.com": "Pinterest", "vimeo.com": "Vimeo",
};
export const REGS: Record<string, string> = {
  registrertIForetaksregisteret: "Register of Business Enterprises", registrertIMvaregisteret: "VAT register",
  registrertIStiftelsesregisteret: "Register of Foundations", registrertIFrivillighetsregisteret: "Register of Non-Profit Organisations",
  registrertIPartiregisteret: "Register of Political Parties",
};
const SIG: Record<string, string> = {
  "styrets leder alene": "the board chair alone", "daglig leder alene": "the general manager alone",
  "to styremedlemmer i fellesskap": "any two board members together", "to av styrets medlemmer i fellesskap": "any two board members together",
  "styrets medlemmer hver for seg": "each board member alone", "styrets leder og ett styremedlem i fellesskap": "the board chair with one board member",
  "daglig leder og styrets leder i fellesskap": "the general manager and the board chair together",
  "daglig leder og ett styremedlem i fellesskap": "the general manager with one board member",
  "daglig leder eller styrets leder hver for seg": "the general manager or the board chair, each alone",
  "styret i fellesskap": "the full board together", "deltakerne hver for seg": "each partner alone", "innehaver": "the owner",
};
/** The signature rule in plain English when every part of it is a known phrase; otherwise nothing. */
export function signText(v: string): string {
  const parts = String(v ?? "").split(/\.\s*/).filter(Boolean);
  const out = parts.map((p) => SIG[p.trim().toLowerCase()]);
  return out.length && out.every(Boolean) ? cap(out.join(", or ")) : "";
}
