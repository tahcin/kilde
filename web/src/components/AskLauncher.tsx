// Ask, from anywhere: a small floating button in the bottom-right corner opens a compact popup (a bottom sheet on
// phones). Pages say what it is about (one company, the compared set); elsewhere the visitor picks companies first.
import * as Dialog from "@radix-ui/react-dialog";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Maximize2, Minimize2, RotateCcw, Sparkles, X } from "lucide-react";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router";
import { useCompare } from "../lib/compare";
import { useCompanies, useIndex, type Company } from "../lib/data";
import { cn, displayName, label, sourceName } from "../lib/format";
import { AskPanel, type Cite } from "./Ask";
import { SourcePop } from "./Source";
import { InlineSearch } from "./Search";
import { Mark } from "./Shell";

type Scope = { companies: Company[]; label: string } | null;
type Ctx = { scope: Scope; setScope: (s: Scope) => void; open: (q?: string) => void; isOpen: boolean; setOpen: (v: boolean) => void;
  pending: string | null; clearPending: () => void };
const C = createContext<Ctx | null>(null);

export function AskProvider({ children }: { children: ReactNode }) {
  const [scope, setScope] = useState<Scope>(null);
  const [isOpen, setOpen] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const value = useMemo(() => ({ scope, setScope, isOpen, setOpen, pending, clearPending: () => setPending(null),
    open: (q?: string) => { if (q) setPending(q); setOpen(true); } }), [scope, isOpen, pending]);
  return <C.Provider value={value}>{children}</C.Provider>;
}

export function useAsk(): Ctx {
  const c = useContext(C);
  if (!c) throw new Error("AskProvider missing");
  return c;
}

/** A page declares what Ask is about while it is shown. */
export function useAskScope(companies: Company[] | undefined | null, label?: string) {
  const { setScope } = useAsk();
  const key = companies?.map((c) => c.o).join(",") ?? "";
  useEffect(() => {
    if (!companies?.length) return;
    setScope({ companies, label: label ?? (companies.length === 1 ? `Ask about ${displayName(companies[0].n)}` : `Ask about these ${companies.length}`) });
    return () => setScope(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, label]);
}

/** A plain text link that opens the popup (used under a company's summary). */
export function AskLink({ children, className }: { children: ReactNode; className?: string }) {
  const { open } = useAsk();
  return (
    <button onClick={() => open()} className={cn("inline-flex items-center gap-1.5 text-[14px] font-medium text-brand underline decoration-brand/30 underline-offset-[3px] hover:decoration-brand", className)}>
      <Sparkles className="size-3.5" aria-hidden />{children}
    </button>
  );
}

const SUGGEST_ONE = ["What does it do?", "Who runs it?", "How did it do in its latest accounts?", "Is it hiring?", "What has changed recently?", "Hvem eier selskapet?"];
const SUGGEST_MANY = ["Which is larger by revenue?", "Compare who runs them", "Which ones are hiring?", "How do their latest results compare?", "Hvilke har verifisert nettside?"];

/** Without a page scope: pick up to three companies first (starting from the compare picks, if any). */
function Picker({ orgs, setOrgs }: { orgs: string[]; setOrgs: (o: string[]) => void }) {
  const { data: rows } = useIndex();
  const name = (o: string) => displayName(rows?.find((r) => r.o === o)?.n ?? o);
  return (
    <div className="mx-auto w-full max-w-[44rem] px-4 pt-4 sm:px-6 sm:pt-6">
      {orgs.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {orgs.map((o) => (
            <span key={o} className="flex items-center gap-1 rounded-full bg-wash py-1 pl-3 pr-1 text-[12.5px] font-medium text-brand">
              {name(o)}
              <button onClick={() => setOrgs(orgs.filter((x) => x !== o))} className="grid size-5 place-items-center rounded-full hover:bg-brand/10" aria-label={`Remove ${name(o)}`}><X className="size-3" /></button>
            </span>
          ))}
        </div>
      )}
      {orgs.length < 3 && <InlineSearch size="md" exclude={orgs} placeholder={orgs.length ? "Add another company (up to 3)" : "Pick a company to ask about"} onPick={(r) => setOrgs([...orgs, r.o])} />}
    </div>
  );
}

function FreeAsk({ panel }: { panel: (cos: Company[]) => ReactNode }) {
  const { picked } = useCompare();
  const [orgs, setOrgs] = useState<string[]>(picked);
  const { data: cos } = useCompanies(orgs);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Picker orgs={orgs} setOrgs={setOrgs} />
      {cos?.length ? panel(cos)
        : <p className="mx-auto w-full max-w-[44rem] px-4 py-6 text-[14px] leading-relaxed text-ink-2 sm:px-6">Pick one to three companies above, then ask in English or Norwegian. Claude answers from Kilde's facts only, with a numbered source on every sentence.</p>}
    </div>
  );
}

const BUBBLE_KEY = "kilde.bubble.closed";
const bubbleClosed = () => { try { return sessionStorage.getItem(BUBBLE_KEY) === "1"; } catch { return false; } };

/** One small speech bubble above the button, its text turning every few seconds through questions worth asking.
 *  Hover pauses it; reduced motion shows a still "Ask Kilde"; closing it hides it for the rest of the visit. */
function Bubble({ items, onPick }: { items: { text: string; ask?: string }[]; onPick: (q?: string) => void }) {
  const reduce = useReducedMotion();
  const [closed, setClosed] = useState(bubbleClosed);
  const [i, setI] = useState(0);
  const [held, setHeld] = useState(false);
  const key = items.map((x) => x.text).join("|");
  useEffect(() => { setI(0); }, [key]);
  useEffect(() => {
    if (reduce || held || items.length < 2) return;
    const t = setInterval(() => { if (!document.hidden) setI((x) => (x + 1) % items.length); }, 3800);
    return () => clearInterval(t);
  }, [reduce, held, key, items.length]);
  if (closed) return null;
  const it = reduce ? items[0] : items[i % items.length];
  return (
    <div className="relative mb-3 mr-1" onPointerEnter={() => setHeld(true)} onPointerLeave={() => setHeld(false)} onFocus={() => setHeld(true)} onBlur={() => setHeld(false)}>
      <button onClick={() => onPick(it.ask)}
        className="relative block min-h-9 max-w-[12.5rem] rounded-2xl bg-card px-3.5 py-2 text-left text-[12.5px] font-medium leading-snug text-brand shadow-[var(--shadow-soft)] ring-1 ring-rule transition hover:ring-brand/40 sm:max-w-[16rem] sm:text-[13.5px]
          after:absolute after:-bottom-[6px] after:right-5 after:size-3 after:rotate-45 after:border-b after:border-r after:border-rule after:bg-card">
        <AnimatePresence mode="wait" initial={false}>
          <motion.span key={it.text} className="block" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.35 }}>
            {it.text}
          </motion.span>
        </AnimatePresence>
      </button>
      <button onClick={() => { setClosed(true); try { sessionStorage.setItem(BUBBLE_KEY, "1"); } catch { /* storage blocked */ } }}
        aria-label="Hide this hint" className="absolute -left-2 -top-2 grid size-5 place-items-center rounded-full bg-card text-muted shadow-[var(--shadow-soft)] ring-1 ring-rule hover:text-ink">
        <X className="size-3" />
      </button>
    </div>
  );
}

/** The expanded view's side panel: the companies in context, and the sources cited in the latest answer. */
function ContextPanel({ companies, cites, lit, setLit }: { companies: Company[]; cites: Cite[]; lit: string | null; setLit: (k: string) => void }) {
  return (
    <aside className="hidden w-[19rem] shrink-0 overflow-y-auto border-l border-rule/70 bg-paper/60 px-6 py-6 scroll-thin md:block" aria-label="Context">
      <p className="text-[12.5px] font-semibold text-ink">Companies</p>
      <ul className="-mx-2 mt-2 space-y-1">
        {companies.map((c) => (
          <li key={c.o}><Link to={`/c/${c.o}`} className="block rounded-lg px-2 py-1.5 text-[13.5px] text-brand hover:bg-hover">{displayName(c.n)}<span className="block font-mono text-[11px] text-muted">{c.o}</span></Link></li>
        ))}
      </ul>
      <p className="mt-6 border-t border-rule/70 pt-6 text-[12.5px] font-semibold text-ink">Sources in this answer</p>
      {cites.length === 0 ? <p className="mt-2 text-[12.5px] text-muted">The numbered sources of the latest answer appear here.</p> : (
        <ol className="-mx-2 mt-2 space-y-1">
          {cites.map((c) => {
            const ev = c.co.ev[c.claim.e[0]];
            return (
              <li key={c.key}>
                <SourcePop co={c.co} claim={c.claim} onOpen={() => setLit(c.key)}
                  className={cn("flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left text-[12.5px] transition hover:bg-hover", lit === c.key && "bg-pop-soft ring-1 ring-pop")}>
                  <span className="mt-px grid h-[18px] min-w-[18px] place-items-center rounded-[5px] bg-pop-soft px-1 text-[11px] font-semibold text-ink ring-1 ring-pop/50">{c.n}</span>
                  <span className="min-w-0">
                    <span className="block text-ink">{label(c.claim.f)}</span>
                    <span className="block truncate text-muted">{ev ? sourceName(c.co.u[ev.u] ?? "", ev.k) : "Checked"}{companies.length > 1 ? ` · ${displayName(c.co.n)}` : ""}</span>
                  </span>
                </SourcePop>
              </li>
            );
          })}
        </ol>
      )}
    </aside>
  );
}

export function AskLauncher() {
  const { scope, isOpen, setOpen, open, pending, clearPending } = useAsk();
  const { picked } = useCompare();
  const { pathname } = useLocation();
  const [expanded, setExpanded] = useState(false);
  const [cites, setCites] = useState<Cite[]>([]);
  const [lit, setLit] = useState<string | null>(null);
  const [reset, setReset] = useState(0);
  const [turnCount, setTurnCount] = useState(0);
  const label = scope?.label ?? "Ask Kilde";
  const trayUp = picked.length > 0 && pathname !== "/compare";
  useEffect(() => { if (!isOpen) setExpanded(false); }, [isOpen]);

  const one = scope?.companies.length === 1 ? scope.companies[0] : null;
  const year = one ? (one.c.find((c) => c.f === "revenue" && c.a === "available" && c.v?.account_type !== "group")?.v?.year as string | undefined) : undefined;
  const hints = one
    ? [{ text: "Ask Kilde" }, { text: `Is ${displayName(one.n)} hiring?`, ask: "Is it hiring?" },
      { text: year ? `How did it do in ${year}?` : "How is it doing?", ask: year ? `How did it do in ${year}?` : "How did it do in its latest accounts?" },
      { text: "Who runs it?", ask: "Who runs it?" }]
    : scope ? [{ text: "Ask Kilde" }, { text: "Which is biggest?", ask: "Which of these is biggest by revenue?" }, { text: "Who is hiring?", ask: "Which of these companies are hiring?" }]
      : [{ text: "Ask Kilde" }, { text: "Ask about any Norwegian company" }];
  const panel = (cos: Company[]) => (
    <AskPanel wide={expanded} companies={cos} suggestions={cos.length > 1 ? SUGGEST_MANY : SUGGEST_ONE}
      autoAsk={pending} onAutoAsked={clearPending} onCitations={setCites} onCite={setLit} lit={lit} resetKey={reset} onTurns={setTurnCount} />
  );

  return (
    <Dialog.Root open={isOpen} onOpenChange={setOpen}>
      <div className={cn("pointer-events-none fixed right-4 z-40 flex flex-col items-end sm:right-6",
        trayUp ? "bottom-[calc(env(safe-area-inset-bottom)+5.25rem)] sm:bottom-[calc(env(safe-area-inset-bottom)+1.5rem)]" : "bottom-[calc(env(safe-area-inset-bottom)+1rem)] sm:bottom-[calc(env(safe-area-inset-bottom)+1.5rem)]",
        isOpen && "opacity-0")}>
        <div className="pointer-events-auto"><Bubble items={hints} onPick={(q) => open(q)} /></div>
        <Dialog.Trigger asChild>
          <button aria-label={label}
            className="pointer-events-auto relative grid size-14 place-items-center rounded-full bg-fam-web shadow-[var(--shadow-float)] ring-1 ring-brand/15 transition hover:bg-wash active:scale-95">
            <Mark />
            <Sparkles className="absolute right-2.5 top-2.5 size-3.5 text-brand" aria-hidden />
          </button>
        </Dialog.Trigger>
      </div>
      <Dialog.Portal>
        <Dialog.Overlay className={cn("fixed inset-0 z-50 transition-opacity duration-300 starting:opacity-0", expanded ? "bg-ink/35" : "bg-ink/20 sm:bg-ink/[0.06]")} />
        <Dialog.Content aria-describedby={undefined}
          onEscapeKeyDown={(e) => { if (expanded) { e.preventDefault(); setExpanded(false); } }}
          className={cn("fixed z-50 flex flex-col overflow-hidden bg-card shadow-[var(--shadow-float)] outline-none ring-1 ring-rule transition-[opacity,translate] duration-300 ease-[var(--ease-out-expo)] starting:translate-y-4 starting:opacity-0",
            expanded
              ? "inset-0 sm:inset-auto sm:left-1/2 sm:top-1/2 sm:h-[85vh] sm:w-[min(80vw,1100px)] sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-[1.4rem]"
              : cn("inset-x-0 bottom-0 max-h-[88dvh] min-h-[60dvh] rounded-t-[1.4rem] pb-[env(safe-area-inset-bottom)]",
                "sm:inset-x-auto sm:bottom-6 sm:right-6 sm:h-[min(560px,calc(100dvh-3rem))] sm:max-h-none sm:min-h-0 sm:w-[400px] sm:rounded-[1.4rem] sm:pb-0"))}>
          {!expanded && <div className="mx-auto mt-2 h-1 w-10 rounded-full bg-rule sm:hidden" aria-hidden />}
          <div className="flex items-center gap-3 border-b border-rule/60 px-4 py-3.5 sm:px-6 sm:py-4">
            <span className="grid size-8 shrink-0 place-items-center rounded-full bg-fam-web"><Mark /></span>
            <div className="min-w-0 flex-1">
              <Dialog.Title className="truncate text-[15.5px] font-semibold text-ink">{label}</Dialog.Title>
              <p className="truncate text-[12px] text-muted">From Kilde's facts, with sources</p>
            </div>
            {turnCount > 0 && (
              <button onClick={() => { setReset((x) => x + 1); setCites([]); setLit(null); }} aria-label="New conversation" title="New conversation"
                className="grid size-8 shrink-0 place-items-center rounded-full text-muted transition hover:bg-hover hover:text-ink">
                <RotateCcw className="size-4" />
              </button>
            )}
            <button title={expanded ? "Back to the small window" : "Expand"} onClick={() => setExpanded(!expanded)} aria-label={expanded ? "Back to the small window" : "Expand"} aria-pressed={expanded}
              className="grid size-8 shrink-0 place-items-center rounded-full text-muted transition hover:bg-hover hover:text-ink">
              {expanded ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
            </button>
            <Dialog.Close className="grid size-8 shrink-0 place-items-center rounded-full text-muted transition hover:bg-hover hover:text-ink" aria-label="Close">
              <X className="size-4" />
            </Dialog.Close>
          </div>
          <div className="flex min-h-0 flex-1">
            <div className="flex min-h-0 min-w-0 flex-1 flex-col">
              {scope ? panel(scope.companies) : <FreeAsk panel={panel} />}
            </div>
            {expanded && scope && <ContextPanel companies={scope.companies} cites={cites} lit={lit} setLit={setLit} />}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
