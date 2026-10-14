// The receipt behind a fact: where it came from, when, the exact words, and how Kilde tied it to this company.
import * as Popover from "@radix-ui/react-popover";
import { ArrowUpRight, FileCheck2, X } from "lucide-react";
import type { ReactNode } from "react";
import type { Claim, Company } from "../lib/data";
import { METHOD, SOURCE_CLASS, STATE, bare, cn, dateTime, date, label, safeUrl, sourceName, toneSoft } from "../lib/format";

export function SourceCard({ co, claim, close }: { co: Company; claim: Claim; close?: ReactNode }) {
  const evs = claim.e.map((i) => co.ev[i]).filter(Boolean);
  const st = STATE[claim.a];
  return (
    <div className="text-[13.5px] leading-relaxed text-ink-2">
      <div className="flex items-start gap-3 border-b border-rule/80 px-4 pb-3 pt-3.5">
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-ink">{label(claim.f)}</p>
          <p className="text-xs text-muted">{co.n} · claim {claim.i}{claim.d ? ` · as of ${date(claim.d)}` : ""}</p>
        </div>
        {claim.a !== "available" && <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-semibold", toneSoft[st.tone])}>{st.word}</span>}
        {close}
      </div>
      {claim.a !== "available" && claim.n && <p className="border-b border-rule/80 px-4 py-3">{claim.n}</p>}
      {evs.length === 0 && <p className="px-4 py-3 text-muted">No saved page backs this entry: it records that the check was made and what it found.</p>}
      {evs.map((ev, n) => {
        const url = co.u[ev.u] ?? "";
        const href = safeUrl(ev.fu) ?? safeUrl(url);
        return (
          <div key={n} className="border-b border-rule/80 px-4 py-3.5 last:border-b-0">
            <div className="flex items-baseline justify-between gap-3">
              <p className="font-medium text-ink">{sourceName(url, ev.k)}</p>
              <span className="shrink-0 text-[11px] text-muted">{SOURCE_CLASS[ev.k] ?? ev.k}{evs.length > 1 ? ` · ${n + 1} of ${evs.length}` : ""}</span>
            </div>
            {ev.q && (
              <figure className="mt-2.5">
                <blockquote className="max-h-40 overflow-y-auto rounded-xl bg-paper px-3 py-2.5 font-mono text-[12px] leading-[1.7] text-ink scroll-thin">
                  <span className={ev.x ? "quote-mark" : ""}>{ev.q}</span>
                </blockquote>
                <figcaption className="mt-1.5 flex items-center gap-1.5 text-[11.5px] text-muted">
                  {ev.x ? <><FileCheck2 className="size-3.5 text-ok" aria-hidden />Exact words from the saved copy of the page</> : "Kilde's description of what the source returned, not a quote"}
                </figcaption>
              </figure>
            )}
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[12.5px]">
              <dt className="text-muted">Retrieved</dt><dd>{dateTime(ev.t)}</dd>
              {ev.d && <><dt className="text-muted">Period</dt><dd>{date(ev.d)}</dd></>}
              <dt className="text-muted">Address</dt>
              <dd className="min-w-0">
                {href ? (
                  <a href={href} target="_blank" rel="noopener noreferrer" className="group inline-flex max-w-full items-center gap-1 text-brand underline decoration-brand/30 underline-offset-2 hover:decoration-brand">
                    <span className="truncate">{bare(ev.fu ?? url)}</span><ArrowUpRight className="size-3 shrink-0" aria-hidden />
                  </a>
                ) : <span className="break-all">{url}</span>}
              </dd>
              <dt className="text-muted">Fingerprint</dt><dd className="font-mono text-[11.5px] text-muted" title="The first 16 characters of the SHA-256 of the saved response">sha256 {ev.h}</dd>
              {ev.l && <><dt className="text-muted">Licence</dt><dd>{ev.l}</dd></>}
            </dl>
            {ev.nt && <p className="mt-2 text-[12px] text-muted">{ev.nt}</p>}
          </div>
        );
      })}
      {claim.m && (
        <div className="bg-wash/60 px-4 py-3 text-[12.5px]">
          <p className="font-medium text-brand">How Kilde tied it to this company</p>
          <p className="mt-0.5 text-ink-2">{METHOD[claim.m] ?? claim.m.replace(/_/g, " ")}</p>
          {claim.md && claim.m !== "registry_endpoint" && <p className="mt-1 break-words text-muted">{claim.md}</p>}
        </div>
      )}
    </div>
  );
}

/** Anything that opens a fact's source. `trigger` is the visible part; it gets the button semantics. */
export function SourcePop({ co, claim, children, className, title }: {
  co: Company; claim: Claim; children: ReactNode; className?: string; title?: string;
}) {
  return (
    <Popover.Root>
      <Popover.Trigger className={className} aria-label={title ?? `Source for ${label(claim.f)}`} aria-haspopup="dialog">
        {children}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content side="bottom" align="end" sideOffset={8} collisionPadding={12}
          className="z-50 max-h-[min(72vh,620px)] w-[min(430px,calc(100vw-24px))] overflow-y-auto overflow-x-hidden rounded-2xl border border-rule/90 bg-card shadow-[var(--shadow-float)] outline-none scroll-thin transition-[opacity,transform] duration-200 ease-[var(--ease-out-expo)] starting:translate-y-1 starting:opacity-0">
          <SourceCard co={co} claim={claim} close={
            <Popover.Close className="-mr-1 grid size-7 shrink-0 place-items-center rounded-full text-muted transition hover:bg-ink/[0.06] hover:text-ink" aria-label="Close source">
              <X className="size-4" />
            </Popover.Close>
          } />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/** The quiet source chip at the end of a fact row: the host and the day it was read. */
export function SourceChip({ co, claim }: { co: Company; claim: Claim }) {
  const ev = co.ev[claim.e[0]];
  const text = ev ? sourceShort(co.u[ev.u] ?? "", ev.k) : "Checked";
  return (
    <SourcePop co={co} claim={claim}
      className="inline-flex h-7 max-w-[11rem] items-center gap-1.5 rounded-full border border-rule bg-card px-2.5 text-[11.5px] font-medium text-muted transition hover:border-brand/40 hover:bg-wash hover:text-brand data-[state=open]:border-brand/50 data-[state=open]:bg-wash data-[state=open]:text-brand">
      <span className="size-1.5 shrink-0 rounded-full bg-pop" aria-hidden />
      <span className="truncate">{text}</span>
    </SourcePop>
  );
}

function sourceShort(url: string, cls: string): string {
  if (/data\.brreg\.no\/regnskapsregisteret/.test(url)) return "Accounts register";
  if (/brreg\.no/.test(url)) return "Brønnøysund";
  if (/nav\.no/.test(url)) return "NAV";
  if (/gleif/.test(url)) return "GLEIF";
  if (/wikidata|qlever/.test(url)) return "Wikidata";
  if (/ted\.europa/.test(url)) return "EU TED";
  if (/forskningsradet/.test(url)) return "Research Council";
  if (/mattilsynet/.test(url)) return "Mattilsynet";
  if (/dibk/.test(url)) return "DiBK";
  if (/finanstilsynet/.test(url)) return "Finanstilsynet";
  void cls;
  return bare(url).split("/")[0];
}
