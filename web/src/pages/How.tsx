import type { ReactNode } from "react";
import { Link } from "react-router";
import { useMeta } from "../lib/data";
import { useTitle } from "../lib/title";
import { STEPS } from "./Home";

const SOURCES: [string, string, string][] = [
  ["Brønnøysund Register Centre (Enhetsregisteret)", "Name, legal form, addresses, industry, employees, share capital, roles, workplaces, group structure, status, and the update feed", "Organisation number in the request"],
  ["Register of Company Accounts (Regnskapsregisteret)", "Filed accounts for the last three years: revenue, results, assets, equity, debt, audit", "Organisation number in the request"],
  ["The company's own website", "Verified website, contact details, social profiles it links to, dated news, careers page", "The site must prove it belongs to the company (below)"],
  ["NAV job ads", "Active job ads with title, place and dates", "Employer organisation number in the ad"],
  ["EU procurement journal (TED)", "Public contracts won, tenders published as a buyer", "Winner or buyer ID equals the organisation number"],
  ["Research Council of Norway", "Funded research projects and SkatteFUNN approvals", "Project owner's organisation number"],
  ["State aid register (Støtteregisteret)", "State aid awards with date, amount and authority", "Recipient's organisation number"],
  ["Mattilsynet", "Food safety inspections and results", "Inspected premises' organisation number"],
  ["DiBK, GLEIF, Finanstilsynet, Wikidata", "Building-work approvals, LEI and parents, financial licences, stock listing", "Organisation number in the record"],
];

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-t border-rule py-10 md:grid md:grid-cols-[14rem_1fr] md:gap-10">
      <h2 className="text-[19px] font-semibold tracking-[-0.02em] text-ink">{title}</h2>
      <div className="mt-3 min-w-0 space-y-3 text-[15.5px] leading-relaxed text-ink-2 md:mt-0 [&>p]:max-w-[75ch]">{children}</div>
    </section>
  );
}

export default function How() {
  useTitle("How it works");
  const { data: meta } = useMeta();
  return (
    <div className="wrap pb-10 pt-10 sm:pt-14">
      <h1 className="display max-w-[18ch] text-[2.4rem] text-ink sm:text-[3.4rem]">How Kilde knows what it tells you</h1>
      <p className="mt-5 max-w-2xl text-[17px] leading-relaxed text-ink-2">
        Kilde is a research agent. Give it a Norwegian organisation number and it builds a profile from public sources, where every fact carries the address it came from,
        when it was read, a fingerprint of the saved page, the exact words, and how it was tied to that company. It uses no language model and costs nothing to run.
      </p>

      <ol className="mt-12 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {STEPS.map((s, i) => (
          <li key={s.title} className="surface rounded-[1.3rem] p-5">
            <div className="flex items-center justify-between"><s.icon className="size-5 text-brand" aria-hidden /><span className="tnum text-[12px] font-semibold text-muted">{i + 1}</span></div>
            <h3 className="mt-4 text-[16px] font-semibold text-ink">{s.title}</h3>
            <p className="mt-2 text-[14px] leading-relaxed text-ink-2">{s.text}</p>
          </li>
        ))}
      </ol>

      <div className="mt-14">
        <Block title="Where the facts come from">
          <div className="-mx-4 overflow-x-auto px-4 scroll-thin sm:mx-0 sm:px-0">
            <table className="w-full min-w-[36rem] text-left text-[14px]">
              <thead><tr className="text-[12.5px] text-muted"><th className="py-2 pr-4 font-medium">Source</th><th className="py-2 pr-4 font-medium">What it gives</th><th className="py-2 font-medium">How it is tied to the company</th></tr></thead>
              <tbody>{SOURCES.map(([a, b, c]) => <tr key={a} className="border-t border-rule/70 align-top"><td className="py-2.5 pr-4 font-medium text-ink">{a}</td><td className="py-2.5 pr-4">{b}</td><td className="py-2.5">{c}</td></tr>)}</tbody>
            </table>
          </div>
          <p>Not used: company-data aggregators whose terms forbid automated copying, search-engine result pages, and scraping of LinkedIn, Facebook or other platforms. Kilde obeys robots.txt on every site, including on redirects.</p>
        </Block>
        <Block title="How a website is proven">
          <p>A website is published only with proof on the site itself: it prints the organisation number; or it shows the phone number from the register in a block naming the company; or the full registered address with the name; or it is the website listed in the register and shows the name. A few stricter name checks cover sites that print none of these.</p>
          <p>Any proof other than the organisation number triggers a search of the register for other companies with the same name, address or phone; if one exists, the site is marked not proven. Social profiles, news and careers pages are taken only from a site that belongs to this company alone.</p>
        </Block>
        <Block title="How well it works">
          <p>Kilde was checked in seventeen rounds, each on a fresh random sample of 100 companies, by auditors separate from the build who compared its output with the live sources by hand. The last three rounds found no facts about the wrong company and no invented figures; the final round alone checked 2,778 items.</p>
          <p>Two runs on the same companies produce identical facts. A run averages about 13 web requests per company, and 1,000 companies took about 12 minutes.</p>
          <p>The main gap is websites: most small Norwegian companies have none, and many sites never print their organisation number. Kilde would rather miss a site than show one it cannot prove{meta ? `; of the ${meta.companies.toLocaleString("en-GB")} companies here, ${meta.families.official_website.companies} have a verified website` : ""}.</p>
        </Block>
        <Block title="Figures are never computed">
          <p>Account figures are copied exactly as filed, with currency, period and whether they are the company's own or group accounts. Kilde never converts, adds or estimates a figure, and the Ask answers are told not to either.</p>
        </Block>
        <Block title="The Ask boxes">
          <p>Questions are answered by Claude Haiku (Anthropic) from Kilde's facts about the companies you picked, and nothing else. Each sentence cites the facts it rests on, and the numbered citations open the same source cards as the rest of the site. If the facts do not answer the question, the answer says so.</p>
          <p>To keep it free to use and cheap to run, each visitor can ask 10 questions every 10 minutes, and the whole site stops answering for the day after 600 questions or an estimated $2. Questions and answers are not stored, and visitors are counted only by a salted daily hash of their address.</p>
        </Block>
        <Block title="Code and data">
          <p>The agent and this website are open source: <a className="text-brand underline underline-offset-2" href="https://github.com/tahcin/kilde" target="_blank" rel="noopener noreferrer">github.com/tahcin/kilde</a>. The licences of each data source are listed at the bottom of every page.</p>
          <p><Link to="/" className="text-brand underline underline-offset-2">Back to the companies</Link></p>
        </Block>
      </div>
    </div>
  );
}
