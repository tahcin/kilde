// Ask, from anywhere: a small floating button in the bottom-right corner opens a compact popup (a bottom sheet on
// phones). Pages say what it is about (one company, the compared set); elsewhere the visitor picks companies first.
import * as Dialog from "@radix-ui/react-dialog";
import { AnimatePresence, motion } from "motion/react";
import { Sparkles, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation } from "react-router";
import { useCompare } from "../lib/compare";
import { useCompanies, useIndex, type Company } from "../lib/data";
import { cn, displayName } from "../lib/format";
import { AskPanel } from "./Ask";
import { InlineSearch } from "./Search";
import { Mark } from "./Shell";

type Scope = { companies: Company[]; label: string } | null;
type Ctx = { scope: Scope; setScope: (s: Scope) => void; open: () => void; isOpen: boolean; setOpen: (v: boolean) => void };
const C = createContext<Ctx | null>(null);

export function AskProvider({ children }: { children: ReactNode }) {
  const [scope, setScope] = useState<Scope>(null);
  const [isOpen, setOpen] = useState(false);
  const value = useMemo(() => ({ scope, setScope, isOpen, setOpen, open: () => setOpen(true) }), [scope, isOpen]);
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
    <button onClick={open} className={cn("inline-flex items-center gap-1.5 text-[14px] font-medium text-brand underline decoration-brand/30 underline-offset-[3px] hover:decoration-brand", className)}>
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

function FreeAsk() {
  const { picked } = useCompare();
  const [orgs, setOrgs] = useState<string[]>(picked);
  const { data: cos } = useCompanies(orgs);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Picker orgs={orgs} setOrgs={setOrgs} />
      {cos?.length ? <AskPanel bare companies={cos} suggestions={cos.length > 1 ? SUGGEST_MANY : SUGGEST_ONE} />
        : <p className="px-5 py-5 text-[14px] leading-relaxed text-ink-2">Pick one to three companies above, then ask in English or Norwegian. Claude answers from Kilde's facts only, with a numbered source on every sentence.</p>}
    </div>
  );
}

export function AskLauncher() {
  const { scope, isOpen, setOpen } = useAsk();
  const { picked } = useCompare();
  const { pathname } = useLocation();
  const label = scope?.label ?? "Ask Kilde";
  // The label shows for a few seconds on each page, or until the visitor scrolls; then just the icon.
  const [expanded, setExpanded] = useState(true);
  useEffect(() => {
    setExpanded(true);
    const t = setTimeout(() => setExpanded(false), 5000);
    const onScroll = () => { if (scrollY > 40) setExpanded(false); };
    addEventListener("scroll", onScroll, { passive: true });
    return () => { clearTimeout(t); removeEventListener("scroll", onScroll); };
  }, [pathname, label]);
  const trayUp = picked.length > 0 && pathname !== "/compare";
  const close = useCallback(() => setOpen(false), [setOpen]);

  return (
    <Dialog.Root open={isOpen} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button aria-label={label}
          className={cn("fixed right-4 z-40 flex h-12 items-center gap-2 rounded-full bg-fam-web pl-3 pr-3 text-[14px] font-semibold text-brand shadow-[var(--shadow-float)] ring-1 ring-brand/15 transition-[bottom,background-color] duration-300 hover:bg-wash sm:right-6",
            trayUp ? "bottom-[calc(env(safe-area-inset-bottom)+5.25rem)] sm:bottom-[calc(env(safe-area-inset-bottom)+1.5rem)]" : "bottom-[calc(env(safe-area-inset-bottom)+1rem)] sm:bottom-[calc(env(safe-area-inset-bottom)+1.5rem)]",
            isOpen && "pointer-events-none opacity-0")}>
          <span className="relative grid size-6 place-items-center"><Mark /><Sparkles className="absolute -right-1.5 -top-1.5 size-3.5 text-brand" aria-hidden /></span>
          <AnimatePresence initial={false}>
            {expanded && (
              <motion.span initial={{ width: 0, opacity: 0 }} animate={{ width: "auto", opacity: 1 }} exit={{ width: 0, opacity: 0 }} transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
                className="overflow-hidden whitespace-nowrap">
                <span className="block max-w-[14rem] truncate pl-0.5 pr-1">{label}</span>
              </motion.span>
            )}
          </AnimatePresence>
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-ink/20 transition-opacity duration-300 starting:opacity-0 sm:bg-ink/[0.06]" />
        <Dialog.Content aria-describedby={undefined}
          className={cn("fixed z-50 flex flex-col overflow-hidden bg-card shadow-[var(--shadow-float)] outline-none ring-1 ring-rule transition-[opacity,translate] duration-300 ease-[var(--ease-out-expo)] starting:translate-y-4 starting:opacity-0",
            "inset-x-0 bottom-0 max-h-[88dvh] min-h-[60dvh] rounded-t-[1.4rem] pb-[env(safe-area-inset-bottom)]",
            "sm:inset-x-auto sm:bottom-6 sm:right-6 sm:h-[min(560px,calc(100dvh-3rem))] sm:max-h-none sm:min-h-0 sm:w-[400px] sm:rounded-[1.4rem] sm:pb-0")}>
          <div className="mx-auto mt-2 h-1 w-10 rounded-full bg-rule sm:hidden" aria-hidden />
          <div className="flex items-center gap-3 px-4 pb-3 pt-3">
            <span className="grid size-8 shrink-0 place-items-center rounded-full bg-fam-web"><Mark /></span>
            <div className="min-w-0 flex-1">
              <Dialog.Title className="truncate text-[15.5px] font-semibold text-ink">{label}</Dialog.Title>
              <p className="text-[12px] text-muted">Answers from Kilde's facts, with sources</p>
            </div>
            <Dialog.Close className="grid size-8 shrink-0 place-items-center rounded-full text-muted transition hover:bg-hover hover:text-ink" aria-label="Close" onClick={close}>
              <X className="size-4" />
            </Dialog.Close>
          </div>
          {scope ? <AskPanel bare companies={scope.companies} suggestions={scope.companies.length > 1 ? SUGGEST_MANY : SUGGEST_ONE} /> : <FreeAsk />}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
