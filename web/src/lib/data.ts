// The published data: index.json for every company, and one gzipped profile per company, fetched when needed.
import { useEffect, useState } from "react";

export type Money = { a: number; c?: string; y?: string; t?: string; i?: string };

/** One row per company (see web/scripts/export_data.py). */
export type Row = {
  o: string; n: string; lf?: string; m?: string; ic?: string; in?: string;
  e?: number | string; w?: string; rev?: Money; res?: Money;
  f: { w: number; s: number; h: number; n: number }; j: number; nf: number; src: string; x?: string;
};

export type State = "available" | "not_available" | "ambiguous" | "blocked" | "failed" | "not_applicable";

/** A claim as Kilde made it. `v` is present only when available. */
export type Claim = {
  i: string; f: string; s: string; a: State; e: number[];
  v?: any;  // eslint-disable-line @typescript-eslint/no-explicit-any -- claim values have many shapes
  d?: string; n?: string; m?: string; md?: string;
};

/** Evidence: u is an index into Company.u. */
export type Evidence = {
  u: number; t: string; q: string; x: 0 | 1; k: string; l: string; h: string; m: string;
  d?: string; nt?: string; fu?: string; sc?: string[] | string;
};

export type Company = {
  o: string; n: string; run: { at: string; commit: string; requests?: number };
  sec: Record<string, State>; sum: [string, string[]][]; unk: string[];
  c: Claim[]; u: string[]; ev: Evidence[];
  /** Claims by id, filled in on load. */
  byId: Record<string, Claim>;
};

export type Meta = {
  companies: number; facts: number; withheld: number;
  families: Record<"official_website" | "social_profile" | "hiring_signal" | "dated_news", { companies: number; facts: number }>;
};

declare const __DATA_VERSION__: string;
const V = `?v=${__DATA_VERSION__}`;

const cache = new Map<string, Promise<unknown>>();

function once<T>(key: string, load: () => Promise<T>): Promise<T> {
  let p = cache.get(key) as Promise<T> | undefined;
  if (!p) {
    p = load();
    p.catch(() => cache.delete(key));
    cache.set(key, p);
  }
  return p;
}

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  // Unknown paths fall back to the app's HTML, so check what came back, not only the status.
  if (!res.ok || (res.headers.get("content-type") ?? "").includes("text/html")) throw new Error(`not found: ${url}`);
  return res.json() as Promise<T>;
}

/** Profiles are stored gzipped. Decompress here unless the server already did (it sent them encoded). */
async function getGz<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok || (res.headers.get("content-type") ?? "").includes("text/html")) throw new Error(`not found: ${url}`);
  const bytes = new Uint8Array(await res.arrayBuffer());
  const text = bytes[0] === 0x1f && bytes[1] === 0x8b
    ? await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"))).text()
    : new TextDecoder().decode(bytes);
  return JSON.parse(text) as T;
}

export const loadIndex = () => once("index", () => getJSON<Row[]>(`/data/index.json${V}`));
export const loadMeta = () => once("meta", () => getJSON<Meta>(`/data/meta.json${V}`));
export const loadCompany = (org: string) => once(`c:${org}`, async () => {
  if (!/^\d{9}$/.test(org)) throw new Error("bad org");
  const co = await getGz<Company>(`/data/c/${org}.json.gz${V}`);
  co.byId = Object.fromEntries(co.c.map((c) => [c.i, c]));
  return co;
});

type Async<T> = { data?: T; error?: Error };

function useAsync<T>(key: string | null, load: () => Promise<T>): Async<T> {
  const [state, setState] = useState<Async<T> & { key?: string | null }>({});
  useEffect(() => {
    if (!key) return;
    let live = true;
    load().then((data) => live && setState({ data, key }), (error) => live && setState({ error, key }));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return state.key === key ? state : {};
}

export const useIndex = () => useAsync("index", loadIndex);
export const useMeta = () => useAsync("meta", loadMeta);
export const useCompany = (org: string | null) => useAsync(org ? `c:${org}` : null, () => loadCompany(org!));

/** Several profiles at once, in the order asked. */
export function useCompanies(orgs: string[]): { data?: Company[]; error?: Error } {
  const key = orgs.join(",");
  return useAsync(key ? `cs:${key}` : null, () => Promise.all(orgs.map(loadCompany)));
}

/* ---------- claim access ---------- */

export const avail = (co: Company, f: string) => co.c.filter((c) => c.f === f && c.a === "available");
export const one = (co: Company, f: string) => avail(co, f)[0] ?? null;
export const anyOf = (co: Company, f: string) => co.c.filter((c) => c.f === f);

export const FIN = ["revenue", "operating_result", "salary_costs", "profit_before_tax", "annual_result", "total_assets",
  "cash_and_bank", "equity", "debt"] as const;

/** Account figures of one kind (company or group), as filed. */
export const accounts = (co: Company, kind: "company" | "group" = "company") =>
  co.c.filter((c) => (FIN as readonly string[]).includes(c.f) && c.a === "available" && c.v && typeof c.v === "object"
    && (c.v.account_type || "company") === kind);

/** The latest filed figure of one field, preferring the company's own accounts. */
export function latest(co: Company, f: string): Claim | null {
  for (const kind of ["company", "group"] as const) {
    const xs = accounts(co, kind).filter((c) => c.f === f && c.v.amount != null)
      .sort((a, b) => String(b.v.year).localeCompare(String(a.v.year)));
    if (xs[0]) return xs[0];
  }
  return null;
}
