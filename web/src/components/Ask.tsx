// Asking about one to three companies. Claude writes the answer from Kilde's facts only; every sentence carries
// numbered citations that open the same source card as the facts on the page.
import { AnimatePresence, motion } from "motion/react";
import { ArrowUp } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { AskError, ask, type Turn } from "../lib/ask";
import type { Company } from "../lib/data";
import { cn, displayName } from "../lib/format";
import { SourcePop } from "./Source";

type Shown = Turn & { status: "streaming" | "done" | "error"; error?: string; code?: string };

const CITE = /\[((?:\d{9}:)?c\d+(?:\s*[,;]\s*(?:\d{9}:)?c\d+)*)\]/g;

/** "[999296432:c24, c28]" -> [[org, id], ...]; an id without an org takes the one before it, or the only company. */
function citeIds(group: string, fallback: string): [string, string][] {
  let org = fallback;
  return group.split(/\s*[,;]\s*/).map((p) => {
    const [a, b] = p.includes(":") ? p.split(":") : [org, p];
    org = a;
    return [a, b] as [string, string];
  });
}

export type Cite = { key: string; n: number; co: Company; claim: import("../lib/data").Claim };

/** The citations of one answer, numbered in order of first appearance; ids not in Kilde's data are dropped. */
export function citeList(text: string, companies: Company[]): Cite[] {
  const byOrg = Object.fromEntries(companies.map((c) => [c.o, c]));
  const out: Cite[] = [];
  const seen = new Set<string>();
  let m: RegExpExecArray | null;
  const re = new RegExp(CITE.source, "g");
  while ((m = re.exec(text))) {
    for (const [o, id] of citeIds(m[1], companies[0]?.o ?? "")) {
      const co = byOrg[o], claim = co?.byId[id], key = `${o}:${id}`;
      if (co && claim && !seen.has(key)) { seen.add(key); out.push({ key, n: out.length + 1, co, claim }); }
    }
  }
  return out;
}

/** Clean up what the model wrote for display: house style has no em dashes. */
const tidy = (s: string) => s.replace(/\s*\u2014\s*/g, ", ").replace(/\*\*(.+?)\*\*/g, "$1");

function AnswerText({ text, companies, streaming, onCite, lit }: { text: string; companies: Company[]; streaming: boolean; onCite?: (key: string) => void; lit?: string | null }) {
  const byOrg = Object.fromEntries(companies.map((c) => [c.o, c]));
  const numbers = new Map<string, number>();
  // While streaming, hold back a half-written citation at the end.
  let shown = tidy(text);
  if (streaming) { const open = shown.lastIndexOf("["); if (open > shown.lastIndexOf("]")) shown = shown.slice(0, open); }
  const lines = shown.split(/\n+/).map((l) => l.trim()).filter(Boolean);

  const inline = (line: string, key: string): ReactNode[] => {
    const out: ReactNode[] = [];
    let last = 0, m: RegExpExecArray | null;
    CITE.lastIndex = 0;
    while ((m = CITE.exec(line))) {
      out.push(line.slice(last, m.index).replace(/\s+$/, ""));
      const chips = citeIds(m[1], companies[0]?.o ?? "").map(([o, id]) => {
        const co = byOrg[o], claim = co?.byId[id];
        if (!co || !claim) return null;  // an id that is not in Kilde's data is never shown as a source
        const k = `${o}:${id}`;
        if (!numbers.has(k)) numbers.set(k, numbers.size + 1);
        return (
          <SourcePop key={k} co={co} claim={claim} title={`Source ${numbers.get(k)}: ${displayName(co.n)}`} onOpen={() => onCite?.(k)}
            className={cn("mx-[1px] inline-grid h-[18px] min-w-[18px] -translate-y-[1px] place-items-center rounded-[5px] bg-pop-soft px-1 align-middle text-[11px] font-semibold text-ink ring-1 ring-pop/50 transition hover:bg-pop data-[state=open]:bg-pop", lit === k && "bg-pop ring-2 ring-brand/50")}>
            {numbers.get(k)}
          </SourcePop>
        );
      }).filter(Boolean);
      if (chips.length) out.push(<span key={`${key}-${m.index}`} className="whitespace-nowrap pl-0.5">{chips}</span>);
      last = m.index + m[0].length;
    }
    out.push(line.slice(last));
    return out;
  };

  const blocks: ReactNode[] = [];
  let list: ReactNode[] = [];
  const flush = () => { if (list.length) { blocks.push(<ul key={`ul${blocks.length}`} className="ml-1 space-y-1.5">{list}</ul>); list = []; } };
  lines.forEach((l, i) => {
    const li = /^(?:[-*•]|\d+[.)])\s+(.*)$/.exec(l);
    if (li) list.push(<li key={i} className="relative pl-4 before:absolute before:left-0 before:top-[0.6em] before:size-1.5 before:rounded-full before:bg-brand/50">{inline(li[1], `l${i}`)}</li>);
    else { flush(); blocks.push(<p key={i}>{inline(l, `p${i}`)}</p>); }
  });
  flush();
  return <div className="space-y-2.5 text-[15px] leading-[1.65] text-ink">{blocks}</div>;
}

function Dots() {
  return (
    <span className="inline-flex gap-1 py-2" aria-label="Writing the answer">
      {[0, 1, 2].map((i) => (
        <motion.span key={i} className="size-1.5 rounded-full bg-brand" animate={{ opacity: [0.25, 1, 0.25] }} transition={{ duration: 1.1, repeat: Infinity, delay: i * 0.18 }} />
      ))}
    </span>
  );
}

export function AskPanel({ companies, suggestions, title, className, intro, autoAsk, onAutoAsked, onCitations, onCite, lit, wide, resetKey, onTurns }: {
  companies: Company[]; suggestions: string[]; title?: string; className?: string; intro?: ReactNode;
  /** Inside the popup: no card or header of its own, and it fills the height it is given. */
  bare?: boolean;
  /** A question to send as soon as the panel is ready (from a teaser bubble). */
  autoAsk?: string | null; onAutoAsked?: () => void;
  /** The citations of the latest answer, for the expanded view's source list. */
  onCitations?: (c: Cite[]) => void; onCite?: (key: string) => void; lit?: string | null;
  /** The expanded reading layout: larger text. */
  wide?: boolean;
  /** Changing this number starts a new conversation (the header's button); onTurns reports how many questions exist. */
  resetKey?: number; onTurns?: (n: number) => void;
}) {
  const orgs = companies.map((c) => c.o);
  const key = orgs.join(",");
  const [turns, setTurns] = useState<Shown[]>([]);
  const [text, setText] = useState("");
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const busy = turns.some((t) => t.status === "streaming");

  // A new set of companies starts a new conversation.
  useEffect(() => { abort.current?.abort(); setTurns([]); }, [key]);
  useEffect(() => () => abort.current?.abort(), []);

  const send = async (q: string) => {
    q = q.trim();
    if (!q || busy || !orgs.length) return;
    const history = turns.filter((t) => t.status === "done").map(({ q, a }) => ({ q, a }));
    const hist = history.slice(-4);
    setText("");
    setTurns((ts) => [...ts, { q, a: "", status: "streaming" }]);
    const ctrl = new AbortController();
    abort.current = ctrl;
    const patch = (fn: (t: Shown) => Shown) => setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? fn(t) : t)));
    try {
      await ask(orgs, q, hist, (piece) => patch((t) => ({ ...t, a: t.a + piece })), ctrl.signal);
      patch((t) => ({ ...t, status: "done" }));
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      const err = e instanceof AskError ? e : new AskError("error", "Something went wrong. Please try again.");
      patch((t) => ({ ...t, status: "error", error: err.message, code: err.code }));
    }
  };
  const last = turns[turns.length - 1];
  useEffect(() => { if (resetKey) { abort.current?.abort(); setTurns([]); input.current?.focus(); } }, [resetKey]);
  useEffect(() => { onTurns?.(turns.length); }, [turns.length]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (autoAsk && orgs.length) { void send(autoAsk); onAutoAsked?.(); } }, [autoAsk, key]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { onCitations?.(last?.a ? citeList(last.a, companies) : []); }, [last?.a, key]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (turns.length) end.current?.scrollIntoView({ block: "nearest" }); }, [turns.length, last?.a.length]);

  const many = companies.length > 1;
  // One spacing scale everywhere in Ask: 16px on phones, 24px from sm up; one centred column for messages and composer.
  const col = "mx-auto w-full max-w-[44rem] px-4 sm:px-6";
  return (
    <section className={cn("flex h-full min-h-0 flex-col", className)} aria-label={title ?? "Ask"}>
      <div className="min-h-0 flex-1 overflow-y-auto scroll-thin" aria-live="polite">
        {turns.length === 0 ? (
          <div className={cn(col, "flex min-h-full flex-col justify-center py-6")}>
            <p className="text-[14.5px] leading-relaxed text-ink-2">{intro ?? "Answered by Claude from Kilde's facts only, with a numbered source on every sentence."}</p>
            <p className="mt-5 text-[12.5px] font-medium text-muted">Try asking</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {suggestions.map((q) => (
                <button key={q} onClick={() => send(q)} disabled={!orgs.length}
                  className="rounded-full border border-rule bg-card px-3.5 py-1.5 text-left text-[13.5px] text-ink-2 transition hover:border-brand/40 hover:bg-wash hover:text-brand disabled:opacity-50">{q}</button>
              ))}
            </div>
          </div>
        ) : (
          <div className={cn(col, "space-y-6 py-4 sm:py-6")}>
            <AnimatePresence initial={false}>
              {turns.map((t, i) => (
                <motion.div key={i} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }} className="space-y-3">
                  <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-brand px-3.5 py-2 text-[14.5px] leading-snug text-on">{t.q}</p>
                  {t.status === "error" ? (
                    <p className={cn("rounded-xl px-3.5 py-2.5 text-[14px] leading-snug", ["ip", "day", "budget", "disabled"].includes(t.code ?? "") ? "bg-amber-soft text-amber" : "bg-bad-soft text-bad")}>{t.error}</p>
                  ) : t.a ? (
                    <div className={cn(wide && "text-[16px]")}><AnswerText text={t.a} companies={companies} streaming={t.status === "streaming"} onCite={onCite} lit={lit} /></div>
                  ) : <Dots />}
                </motion.div>
              ))}
            </AnimatePresence>
            <div ref={end} />
          </div>
        )}
      </div>

      <form onSubmit={(e) => { e.preventDefault(); void send(text); }} className={cn(col, "pb-4 pt-3 sm:pb-6")}>
        <div className="flex items-end gap-2 rounded-2xl border border-rule bg-paper/70 p-1.5 pl-4 transition focus-within:border-brand/50 focus-within:bg-card focus-within:shadow-[0_0_0_4px_color-mix(in_srgb,var(--color-brand)_10%,transparent)]">
          <label className="sr-only" htmlFor={`ask-${key}`}>Your question</label>
          <textarea id={`ask-${key}`} ref={input} rows={1} value={text} maxLength={500} onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(text); } }}
            placeholder={orgs.length ? (many ? "Ask about these companies" : "Ask a question, in English or Norwegian") : "Pick a company first"}
            disabled={!orgs.length}
            className="max-h-32 min-h-[2.25rem] flex-1 resize-none bg-transparent py-1.5 text-[15px] leading-snug text-ink outline-none [field-sizing:content] placeholder:text-muted disabled:cursor-not-allowed" />
          <button type="submit" disabled={busy || !text.trim()} aria-label="Ask"
            className={cn("grid size-9 shrink-0 place-items-center rounded-xl transition", text.trim() && !busy ? "bg-brand text-on hover:bg-brand-2" : "bg-paper-2 text-muted/70")}>
            <ArrowUp className="size-4" strokeWidth={2.4} aria-hidden />
          </button>
        </div>
        <p className="mt-2 text-[11.5px] leading-snug text-muted">
          Answers are generated by Claude (Anthropic) from Kilde's verified facts and can still be wrong: check the numbered sources.
          {turns.length >= 4 && " Only the last four questions are remembered."}
        </p>
      </form>
    </section>
  );
}

