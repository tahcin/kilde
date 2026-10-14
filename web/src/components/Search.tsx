// Finding a company: by name, organisation number, town, industry or website. One matcher, two dresses: the large
// box on the home page (and the compare picker), and the ⌘K palette from the header.
import * as Dialog from "@radix-ui/react-dialog";
import { Command } from "cmdk";
import { CornerDownLeft, Search as SearchIcon } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useIndex, type Row } from "../lib/data";
import { LEGAL_FORM, cn, displayName, industry, orgFmt, titleCase } from "../lib/format";

const fold = (s: string) => s.toLowerCase().replace(/ø/g, "o").replace(/æ/g, "ae").replace(/å/g, "a").normalize("NFD").replace(/[̀-ͯ]/g, "");

type Hay = { row: Row; name: string; words: string[]; rest: string; org: string };
let hayCache: { rows: Row[]; hay: Hay[] } | null = null;
function hayOf(rows: Row[]): Hay[] {
  if (hayCache?.rows === rows) return hayCache.hay;
  const hay = rows.map((row) => {
    const name = fold(row.n);
    return { row, name, words: name.split(/[\s\-&./]+/).filter(Boolean), org: row.o,
      rest: fold([row.m, row.in, industry(row.ic, row.in), row.w, LEGAL_FORM[row.lf ?? ""]].filter(Boolean).join(" ")) };
  });
  hayCache = { rows, hay };
  return hay;
}

/** Best matches first: name start, then a word start, then anywhere in the name, then town, industry or website. */
export function searchRows(rows: Row[], query: string, limit = 30): Row[] {
  const q = fold(query.trim());
  if (!q) return [];
  const digits = q.replace(/\s/g, "");
  const terms = q.split(/\s+/).filter(Boolean);
  const scored: [number, Hay][] = [];
  for (const h of hayOf(rows)) {
    let s = 0;
    if (/^\d{3,9}$/.test(digits) && h.org.startsWith(digits)) s = 100;
    else if (h.name.startsWith(q)) s = 80;
    else if (h.words.some((w) => w.startsWith(q))) s = 60;
    else if (h.name.includes(q)) s = 40;
    else if (terms.every((t) => h.name.includes(t) || h.rest.includes(t))) s = 20;
    if (s) scored.push([s + Math.min(h.row.nf, 400) / 400 - h.name.length / 1000, h]);
  }
  return scored.sort((a, b) => b[0] - a[0]).slice(0, limit).map(([, h]) => h.row);
}

export function rowLine(r: Row): string {
  return [LEGAL_FORM[r.lf ?? ""] ?? r.lf, r.m && titleCase(r.m), industry(r.ic, r.in)].filter(Boolean).join(" · ");
}

const item = "group flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors duration-150 data-[selected=true]:bg-wash";

function ResultItem({ r, onSelect }: { r: Row; onSelect: () => void }) {
  return (
    <Command.Item value={r.o} onSelect={onSelect} className={item}>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[15px] font-medium text-ink">{displayName(r.n)}</span>
        <span className="block truncate text-[12.5px] text-muted">{rowLine(r)}</span>
      </span>
      <span className="hidden font-mono text-[11.5px] text-muted sm:block">{orgFmt(r.o)}</span>
      <CornerDownLeft className="size-3.5 shrink-0 text-brand opacity-0 transition group-data-[selected=true]:opacity-100" aria-hidden />
    </Command.Item>
  );
}

/** The search box with its results list underneath. `onPick` overrides going to the company page. */
export function InlineSearch({ onPick, placeholder = "Name, org number or town", autoFocus, size = "lg", exclude = [] }: {
  onPick?: (r: Row) => void; placeholder?: string; autoFocus?: boolean; size?: "lg" | "md"; exclude?: string[];
}) {
  const { data: rows } = useIndex();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const hits = useMemo(() => (rows ? searchRows(rows, q, 12).filter((r) => !exclude.includes(r.o)) : []), [rows, q, exclude]);
  const box = useRef<HTMLDivElement>(null);
  const pick = (r: Row) => { setQ(""); setOpen(false); if (onPick) onPick(r); else navigate(`/c/${r.o}`); };
  const lg = size === "lg";
  return (
    <div ref={box} className="relative" onBlur={(e) => { if (!box.current?.contains(e.relatedTarget as Node)) setOpen(false); }}>
      <Command label="Search companies" shouldFilter={false} loop className="relative">
        <div className={cn("flex items-center gap-3 border bg-card transition-[border-color,box-shadow] duration-300 focus-within:border-brand/50 focus-within:shadow-[var(--shadow-float),0_0_0_4px_color-mix(in_srgb,var(--color-brand)_12%,transparent)]",
          lg ? "h-16 rounded-2xl border-rule px-5 shadow-[var(--shadow-float)]" : "h-12 rounded-xl border-rule px-4 shadow-[var(--shadow-soft)]",
          open && q && "rounded-b-none")}>
          <SearchIcon className={cn("shrink-0 text-brand", lg ? "size-5" : "size-4")} aria-hidden />
          <Command.Input value={q} onValueChange={(v) => { setQ(v); setOpen(true); }} onFocus={() => setOpen(true)} autoFocus={autoFocus}
            onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}
            placeholder={rows ? placeholder : "Loading companies…"}
            className={cn("h-full min-w-0 flex-1 bg-transparent text-ink outline-none placeholder:text-muted", lg ? "text-[17px]" : "text-[15px]")} />
          {lg && rows && <span className="hidden shrink-0 text-xs text-muted sm:block">{rows.length.toLocaleString("en-GB")} companies</span>}
        </div>
        {open && q && (
          <Command.List className="absolute inset-x-0 top-full z-30 max-h-[min(60vh,440px)] overflow-y-auto overscroll-contain rounded-b-2xl border border-t-0 border-rule bg-card p-1.5 shadow-[var(--shadow-float)] scroll-thin">
            <Command.Empty className="px-4 py-6 text-center text-sm text-muted">No company matches “{q}”. Try part of the name, or the 9-digit organisation number.</Command.Empty>
            {hits.map((r) => <ResultItem key={r.o} r={r} onSelect={() => pick(r)} />)}
          </Command.List>
        )}
      </Command>
    </div>
  );
}

export function SearchPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const { data: rows } = useIndex();
  const [q, setQ] = useState("");
  const navigate = useNavigate();
  const hits = useMemo(() => (rows ? searchRows(rows, q, 40) : []), [rows, q]);
  const go = (o: string) => { onOpenChange(false); setQ(""); navigate(`/c/${o}`); };
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-ink/25 backdrop-blur-[3px] transition-opacity duration-300 starting:opacity-0" />
        <Dialog.Content className="fixed left-1/2 top-[10vh] z-50 w-[min(640px,calc(100vw-1.5rem))] -translate-x-1/2 overflow-hidden rounded-[1.4rem] border border-rule/80 bg-card shadow-[var(--shadow-float)] outline-none transition-[opacity,scale] duration-300 ease-[var(--ease-out-expo)] starting:scale-[0.97] starting:opacity-0">
          <Dialog.Title className="sr-only">Find a company</Dialog.Title>
          <Dialog.Description className="sr-only">Type a name, organisation number, town or industry, then press Enter to open the company.</Dialog.Description>
          <Command label="Find a company" shouldFilter={false} loop>
            <div className="flex items-center gap-3 border-b border-rule/80 px-5">
              <SearchIcon className="size-[18px] shrink-0 text-brand" aria-hidden />
              <Command.Input autoFocus value={q} onValueChange={setQ} placeholder="Find a company: name, org number, town"
                className="h-[3.6rem] min-w-0 flex-1 bg-transparent text-[16px] text-ink outline-none placeholder:text-muted" />
              <button type="button" onClick={() => onOpenChange(false)} className="rounded-md border border-rule bg-paper px-1.5 py-0.5 text-[11px] text-muted hover:text-ink" aria-label="Close">esc</button>
            </div>
            <Command.List className="max-h-[min(58vh,460px)] overflow-y-auto overscroll-contain p-1.5 scroll-thin">
              {!q && <p className="px-4 py-6 text-sm text-muted">{rows ? `Search ${rows.length.toLocaleString("en-GB")} Norwegian companies Kilde has profiled.` : "Loading companies…"}</p>}
              {q && <Command.Empty className="px-4 py-6 text-center text-sm text-muted">No company matches “{q}”.</Command.Empty>}
              {hits.map((r) => <ResultItem key={r.o} r={r} onSelect={() => go(r.o)} />)}
            </Command.List>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
