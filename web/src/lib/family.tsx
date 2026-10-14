// One pastel tint per fact family, used the same way everywhere: chips, tiles, section markers, the browse list.
import { cn } from "./format";

export type Fam = "web" | "social" | "hiring" | "news" | "records" | "accounts" | "people";

export const FAM: Record<Fam, { bg: string; ink: string; dot: string; label: string }> = {
  web: { bg: "bg-fam-web", ink: "text-fam-web-ink", dot: "bg-fam-web-ink", label: "Website" },
  social: { bg: "bg-fam-social", ink: "text-fam-social-ink", dot: "bg-fam-social-ink", label: "Social" },
  hiring: { bg: "bg-fam-hiring", ink: "text-fam-hiring-ink", dot: "bg-fam-hiring-ink", label: "Hiring" },
  news: { bg: "bg-fam-news", ink: "text-fam-news-ink", dot: "bg-fam-news-ink", label: "News" },
  records: { bg: "bg-fam-records", ink: "text-fam-records-ink", dot: "bg-fam-records-ink", label: "Public records" },
  accounts: { bg: "bg-fam-accounts", ink: "text-fam-accounts-ink", dot: "bg-fam-accounts-ink", label: "Accounts" },
  people: { bg: "bg-fam-people", ink: "text-fam-people-ink", dot: "bg-fam-people-ink", label: "People" },
};

export function FamChip({ fam, children, className }: { fam: Fam; children?: React.ReactNode; className?: string }) {
  const f = FAM[fam];
  return <span className={cn("inline-flex items-center rounded-md px-1.5 py-0.5 text-[11.5px] font-medium", f.bg, f.ink, className)}>{children ?? f.label}</span>;
}
