import { money, moneyTitle, cn } from "../lib/format";

/** A compact, readable figure with the exact filed value on hover (and in its source card). */
export function Money({ currency, n, className }: { currency?: string; n: number | string; className?: string }) {
  const m = money(currency, n);
  return <span className={cn(Number(n) < 0 && "text-bad", className)} title={moneyTitle(m)}>{m.label}</span>;
}
