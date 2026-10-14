// Ask, from anywhere: a small floating button in the bottom-right corner opens a compact popup (a bottom sheet on
// phones). Pages say what it is about (one company, the compared set); elsewhere the visitor picks companies first.
import * as Dialog from "@radix-ui/react-dialog";
import { AnimatePresence, motion } from "motion/react";
import { Maximize2, MessageCircle, Minimize2, Sparkles, X } from "lucide-react";
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
    <div className="border-b border-rule/70 px-4 pb-3">
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
        : <p className="px-5 py-5 text-[14px] leading-relaxed text-ink-2">Pick one to three companies above, then ask in English or Norwegian. Claude answers from Kilde's facts only, with a numbered source on every sentence.</p>}
    </div>
  );
}

const TEASE_KEY = "kilde.teasers";
const readTease = (): { dismissed?: boolean; shown?: number } => { try { return JSON.parse(sessionStorage.getItem(TEASE_KEY) ?? "{}"); } catch { return {}; } };
const writeTease = (v: { dismissed?: boolean; shown?: number }) => { try { sessionStorage.setItem(TEASE_KEY, JSON.stringify(v)); } catch { /* storage blocked */ } };

/** Small chat-preview bubbles above the launcher, a moment after the page loads. Calm: once a session,
 *  gone after ten seconds or the first scroll, never again once closed. */
function Teasers({ items, onPick }: { items: { text: string; ask?: string }[]; onPick: (q?: string) => void }) {
  const [show, setShow] = useState(false);
  const { pathname } = useLocation();
  useEffect(() => {
    setShow(false);
    const t0 = readTease();
    if (t0.dismissed || (t0.shown ?? 0) >= 1) return;
    const t = setTimeout(() => { setShow(true); writeTease({ ...readTease(), shown: (readTease().shown ?? 0) + 1 }); }, 2500);
    const hide = setTimeout(() => setShow(false), 12500);
    const onScroll = () => { if (scrollY > 80) setShow(false); };
    addEventListener("scroll", onScroll, { passive: true });
    return () => { clearTimeout(t); clearTimeout(hide); removeEventListener("scroll", onScroll); };
  }, [pathname, items.map((i) => i.text).join("|")]);  // eslint-disable-line react-hooks/exhaustive-deps
  const dismiss = () => { setShow(false); writeTease({ ...readTease(), dismissed: true }); };
  return (
    <AnimatePresence>
      {show && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
          className="relative mb-3 flex max-w-[15.5rem] flex-col items-end gap-1.5 sm:max-w-[18rem]" role="complementary" aria-label="Suggested questions">
          <button onClick={dismiss} aria-label="Hide suggestions"
            className="absolute -left-2 -top-2 z-10 grid size-6 place-items-center rounded-full bg-card text-muted shadow-[var(--shadow-soft)] ring-1 ring-rule hover:text-ink">
            <X className="size-3.5" />
          </button>
          {items.map((it, i) => (
            <motion.button key={it.text} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.25 + i * 0.35, duration: 0.4 }}
              onClick={() => { dismiss(); onPick(it.ask); }}
              className={cn("rounded-2xl px-3.5 py-2 text-left text-[13px] leading-snug shadow-[var(--shadow-soft)] ring-1 transition hover:ring-brand/40 sm:text-[13.5px]",
                i === items.length - 1 ? "rounded-br-md" : "", it.ask ? "bg-card text-brand ring-rule font-medium" : "bg-fam-web text-ink ring-brand/10")}>
              {it.text}
            </motion.button>
          ))}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/** The expanded view's side panel: the companies in context, and the sources cited in the latest answer. */
function ContextPanel({ companies, cites, lit, setLit }: { companies: Company[]; cites: Cite[]; lit: string | null; setLit: (k: string) => void }) {
  return (
    <aside className="hidden w-[19rem] shrink-0 overflow-y-auto border-l border-rule/70 bg-paper/60 p-4 scroll-thin md:block" aria-label="Context">
      <p className="text-[12.5px] font-semibold text-ink">Companies</p>
      <ul className="mt-2 space-y-1">
        {companies.map((c) => (
          <li key={c.o}><Link to={`/c/${c.o}`} className="block rounded-lg px-2 py-1.5 text-[13.5px] text-brand hover:bg-hover">{displayName(c.n)}<span className="block font-mono text-[11px] text-muted">{c.o}</span></Link></li>
        ))}
      </ul>
      <p className="mt-5 text-[12.5px] font-semibold text-ink">Sources in this answer</p>
      {cites.length === 0 ? <p className="mt-2 text-[12.5px] text-muted">The numbered sources of the latest answer appear here.</p> : (
        <ol className="mt-2 space-y-1">
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
  const label = scope?.label ?? "Ask Kilde";
  // The button says who it is about when the name is short enough to fit; otherwise just "Ask Kilde".
  const buttonLabel = scope && scope.companies.length === 1 && label.length <= 30 ? label : scope && scope.companies.length > 1 ? label : "Ask Kilde";
  const trayUp = picked.length > 0 && pathname !== "/compare";
  useEffect(() => { if (!isOpen) setExpanded(false); }, [isOpen]);

  const one = scope?.companies.length === 1 ? displayName(scope.companies[0].n) : null;
  const teasers = scope ? (one
    ? [{ text: `Ask me about ${one}` }, { text: "Is it hiring?", ask: "Is it hiring?" }, { text: "How did it do in its latest accounts?", ask: "How did it do in its latest accounts?" }].slice(0, 2)
    : [{ text: "Which of these is biggest?", ask: "Which of these is biggest by revenue?" }])
    : [{ text: "Ask about any Norwegian company" }];
  const panel = (cos: Company[]) => (
    <AskPanel bare wide={expanded} companies={cos} suggestions={cos.length > 1 ? SUGGEST_MANY : SUGGEST_ONE}
      autoAsk={pending} onAutoAsked={clearPending} onCitations={setCites} onCite={setLit} lit={lit} />
  );

  return (
    <Dialog.Root open={isOpen} onOpenChange={setOpen}>
      <div className={cn("pointer-events-none fixed right-4 z-40 flex flex-col items-end sm:right-6",
        trayUp ? "bottom-[calc(env(safe-area-inset-bottom)+5.25rem)] sm:bottom-[calc(env(safe-area-inset-bottom)+1.5rem)]" : "bottom-[calc(env(safe-area-inset-bottom)+1rem)] sm:bottom-[calc(env(safe-area-inset-bottom)+1.5rem)]",
        isOpen && "opacity-0")}>
        <div className="pointer-events-auto"><Teasers items={teasers} onPick={(q) => open(q)} /></div>
        <Dialog.Trigger asChild>
          {/* A speech bubble: rounded, with a small tail at the bottom right, always showing its label. */}
          <button aria-label={label}
            className="pointer-events-auto relative flex h-12 max-w-[calc(100vw-2rem)] items-center gap-2 rounded-[1.4rem] rounded-br-[0.4rem] bg-brand pl-4 pr-5 text-[14.5px] font-semibold text-on shadow-[0_10px_28px_-8px_rgb(31_86_115/0.55),0_2px_6px_rgb(28_38_43/0.12)] transition hover:bg-brand-2 active:scale-[0.97]
              after:absolute after:-bottom-[7px] after:right-0 after:size-0 after:border-l-[12px] after:border-t-[8px] after:border-l-transparent after:border-t-brand hover:after:border-t-brand-2">
            <MessageCircle className="size-5 shrink-0" strokeWidth={2.2} aria-hidden />
            <span className="truncate">{buttonLabel}</span>
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
          <div className="flex items-center gap-3 border-b border-rule/60 px-4 pb-3 pt-3">
            <span className="grid size-8 shrink-0 place-items-center rounded-full bg-fam-web"><Mark /></span>
            <div className="min-w-0 flex-1">
              <Dialog.Title className="truncate text-[15.5px] font-semibold text-ink">{label}</Dialog.Title>
              <p className="text-[12px] text-muted">Answers from Kilde's facts, with sources</p>
            </div>
            <button onClick={() => setExpanded(!expanded)} aria-label={expanded ? "Back to the small window" : "Expand"} aria-pressed={expanded}
              className="grid size-8 shrink-0 place-items-center rounded-full text-muted transition hover:bg-hover hover:text-ink">
              {expanded ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
            </button>
            <Dialog.Close className="grid size-8 shrink-0 place-items-center rounded-full text-muted transition hover:bg-hover hover:text-ink" aria-label="Close">
              <X className="size-4" />
            </Dialog.Close>
          </div>
          <div className="flex min-h-0 flex-1">
            <div className={cn("flex min-h-0 min-w-0 flex-1 flex-col", expanded && "sm:px-4")}>
              {scope ? panel(scope.companies) : <FreeAsk panel={panel} />}
            </div>
            {expanded && scope && <ContextPanel companies={scope.companies} cites={cites} lit={lit} setLit={setLit} />}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
