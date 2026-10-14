import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, Columns3, Search, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, ScrollRestoration, useLocation } from "react-router";
import { useCompare } from "../lib/compare";
import { useIndex } from "../lib/data";
import { cn, displayName } from "../lib/format";
import { SearchPalette } from "./Search";
import { AskLauncher } from "./AskLauncher";

/** Kilde's mark, the same as in its report: an accent square with a lighter inset corner. */
export function Mark({ className }: { className?: string }) {
  return <span className={cn("kilde-mark", className)} aria-hidden />;
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-[9px]", className)}>
      <Mark />
      <span className="text-[20px] font-bold tracking-[-0.02em] text-ink">Kilde</span>
    </span>
  );
}

function CompareTray() {
  const { picked, remove, clear } = useCompare();
  const { data: rows } = useIndex();
  const { pathname } = useLocation();
  const show = picked.length > 0 && pathname !== "/compare";
  const name = (o: string) => displayName(rows?.find((r) => r.o === o)?.n ?? o);
  return (
    <AnimatePresence>
      {show && (
        <motion.div initial={{ y: 90, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 90, opacity: 0 }} transition={{ type: "spring", bounce: 0.2, duration: 0.6 }}
          className="pointer-events-none fixed inset-x-0 bottom-3 z-40 flex justify-center px-3 sm:bottom-5" role="region" aria-label="Companies picked to compare">
          <div className="glass pointer-events-auto flex max-w-full items-center gap-2 rounded-2xl border border-rule/80 p-1.5 pl-3 shadow-[var(--shadow-float)]">
            <Columns3 className="hidden size-4 shrink-0 text-brand sm:block" aria-hidden />
            <div className="flex min-w-0 gap-1.5 overflow-x-auto scroll-thin">
              {picked.map((o) => (
                <span key={o} className="flex shrink-0 items-center gap-1 rounded-full bg-wash py-1 pl-2.5 pr-1 text-[12.5px] font-medium text-brand">
                  <span className="max-w-[9rem] truncate">{name(o)}</span>
                  <button onClick={() => remove(o)} className="grid size-5 place-items-center rounded-full hover:bg-brand/10" aria-label={`Remove ${name(o)}`}><X className="size-3" /></button>
                </span>
              ))}
            </div>
            {picked.length >= 2 ? (
              <Link to={`/compare?o=${picked.join(",")}`} className="flex h-9 shrink-0 items-center gap-1.5 rounded-xl bg-brand px-3.5 text-[13.5px] font-medium text-on transition hover:bg-brand-2">
                Compare<ArrowRight className="size-3.5" aria-hidden />
              </Link>
            ) : <span className="shrink-0 px-1 text-[12.5px] text-muted">Pick one more</span>}
            <button onClick={clear} className="hidden h-9 shrink-0 rounded-xl px-2.5 text-[12.5px] text-muted hover:bg-ink/[0.05] hover:text-ink sm:block">Clear</button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function Shell() {
  const [searching, setSearching] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const { picked } = useCompare();
  const { pathname } = useLocation();
  useEffect(() => {
    const onScroll = () => setScrolled(scrollY > 16);
    onScroll();
    addEventListener("scroll", onScroll, { passive: true });
    return () => removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setSearching((s) => !s); }
      else if (e.key === "/" && !/input|textarea/i.test((e.target as HTMLElement).tagName)) { e.preventDefault(); setSearching(true); }
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, []);
  const link = ({ isActive }: { isActive: boolean }) => cn("rounded-full px-3.5 py-1.5 text-[14px] font-medium transition-colors", isActive ? "bg-ink/[0.07] text-ink" : "text-ink-2 hover:text-ink");
  return (
    <div className="flex min-h-dvh flex-col bg-paper text-ink">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-card focus:px-3 focus:py-2">Skip to content</a>
      <header className="sticky top-0 z-40 pt-2 sm:pt-3">
        <div className="wrap">
        <div className={cn("-mx-2.5 flex h-14 items-center gap-2 rounded-2xl border px-2.5 transition-[background-color,border-color,box-shadow] duration-500 ease-[var(--ease-out-expo)]",
          scrolled ? "glass border-rule/70 shadow-[var(--shadow-soft)]" : "border-transparent")}>
          <Link to="/" aria-label="Kilde, home" className="rounded-lg"><Wordmark /></Link>
          <nav className="ml-4 hidden items-center gap-0.5 md:flex" aria-label="Main">
            {/* Active by section: Companies covers the list and every company page. */}
            {[["/", "Companies", pathname === "/" || pathname.startsWith("/c/")],
              [picked.length ? `/compare?o=${picked.join(",")}` : "/compare", "Compare", pathname === "/compare"],
              ["/how", "How it works", pathname === "/how"]].map(([to, text, on]) => (
              <Link key={text as string} to={to as string} aria-current={on ? "page" : undefined} className={link({ isActive: !!on })}>
                {text as string}{text === "Compare" && picked.length > 0 && <span className="ml-1.5 inline-grid size-5 place-items-center rounded-full bg-brand text-[11px] text-on">{picked.length}</span>}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-1.5">
            <button onClick={() => setSearching(true)} aria-label="Find a company"
              className="flex h-10 items-center gap-2 rounded-xl border border-rule/90 bg-card/80 px-3 text-sm text-muted transition hover:border-brand/40 hover:text-ink">
              <Search className="size-4" aria-hidden /><span className="hidden sm:inline">Find a company</span>
              <kbd className="ml-2 hidden rounded-md border border-rule bg-paper px-1.5 py-0.5 font-sans text-[11px] text-muted lg:inline">⌘K</kbd>
            </button>
            <NavLink to={picked.length ? `/compare?o=${picked.join(",")}` : "/compare"} aria-label="Compare"
              className="relative grid size-10 place-items-center rounded-xl border border-rule/90 bg-card/80 text-ink-2 md:hidden">
              <Columns3 className="size-4" aria-hidden />
              {picked.length > 0 && <span className="absolute -right-1 -top-1 grid size-4.5 place-items-center rounded-full bg-brand text-[10px] text-on">{picked.length}</span>}
            </NavLink>
          </div>
        </div>
        </div>
      </header>
      <main id="main" className="flex-1"><Outlet /></main>
      <Footer />
      <CompareTray />
      <AskLauncher />
      <SearchPalette open={searching} onOpenChange={setSearching} />
      <ScrollRestoration />
    </div>
  );
}

function Footer() {
  const a = "underline decoration-current/30 underline-offset-[3px] transition hover:text-ink hover:decoration-current";
  return (
    <footer className="mt-16 border-t border-rule bg-paper-2/50">
      <div className="wrap grid gap-10 py-12 text-[13.5px] leading-relaxed text-muted md:grid-cols-[1.1fr_1fr_1fr]">
        <div>
          <Wordmark />
          <p className="mt-3 max-w-xs">Kilde means source. A company research agent built by team Solus for the{" "}
            <a className={a} href="https://builderr.ai/challenges/signalpost" target="_blank" rel="noopener noreferrer">Builderr Signalpost challenge</a>.</p>
          <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
            <a className={a} href="https://github.com/tahcin/kilde" target="_blank" rel="noopener noreferrer">Code on GitHub</a>
            <Link className={a} to="/how">How it works</Link>
          </p>
        </div>
        <div>
          <h2 className="font-semibold text-ink">Data and licences</h2>
          <p className="mt-2">
            Contains data under the Norwegian licence for Open Government data (NLOD 2.0) distributed by Brønnøysundregistrene, and Research Council of Norway data (NLOD).
            Industry and legal-form names from Statistics Norway (SSB) Klass, and Mattilsynet inspections, CC BY 4.0. Website candidates from OpenStreetMap (ODbL 1.0) and Overture Maps (CDLA-Permissive 2.0).
            GLEIF and Wikidata, CC0. EU TED notices may be reused freely; NAV job ads under NAV's API terms.
          </p>
        </div>
        <div>
          <h2 className="font-semibold text-ink">About the answers</h2>
          <p className="mt-2">
            The profiles are built without any language model. Answers in the Ask boxes are generated by Claude (Anthropic) from Kilde's verified facts only, and cite them;
            they can still be wrong, so check the sources. Questions are not stored.
          </p>
          <p className="mt-3">Profiles retrieved on 14 October 2026. Kilde is independent and not affiliated with any register or company shown.</p>
        </div>
      </div>
    </footer>
  );
}
