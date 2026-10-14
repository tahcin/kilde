// Cost guards for /api/ask, as pure functions so they can be tested without Cloudflare or the API.
// The Durable Object in index.ts keeps the state; this file only decides.

export type Limits = {
  perIp: number;        // questions per visitor in one window
  windowMs: number;     // the per-visitor window
  perDay: number;       // questions per UTC day for the whole site
  usdPerDay: number;    // estimated spend per UTC day for the whole site
};

export const DEFAULT_LIMITS: Limits = { perIp: 10, windowMs: 10 * 60_000, perDay: 600, usdPerDay: 2 };

export type DayState = { day: string; count: number; usd: number };

export type Verdict =
  | { ok: true }
  | { ok: false; reason: "ip" | "day" | "budget"; retryAfter: number };

export const utcDay = (now: number) => new Date(now).toISOString().slice(0, 10);

/** Seconds until the next UTC midnight. */
export function untilMidnight(now: number): number {
  const d = new Date(now);
  const next = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + 1);
  return Math.ceil((next - now) / 1000);
}

/** The day's counters, reset when the UTC day has turned. */
export function today(state: DayState | undefined, now: number): DayState {
  const day = utcDay(now);
  return state && state.day === day ? state : { day, count: 0, usd: 0 };
}

/**
 * May this visitor ask now? `hits` are the visitor's earlier question times (any age; old ones are ignored).
 * The site-wide caps come first, so a visitor is never told to wait ten minutes when the site is closed for the day.
 */
export function decide(state: DayState, hits: number[], now: number, limits: Limits = DEFAULT_LIMITS): Verdict {
  if (state.usd >= limits.usdPerDay) return { ok: false, reason: "budget", retryAfter: untilMidnight(now) };
  if (state.count >= limits.perDay) return { ok: false, reason: "day", retryAfter: untilMidnight(now) };
  const recent = hits.filter((t) => t > now - limits.windowMs).sort((a, b) => a - b);
  if (recent.length >= limits.perIp) {
    const oldest = recent[recent.length - limits.perIp];
    return { ok: false, reason: "ip", retryAfter: Math.max(1, Math.ceil((oldest + limits.windowMs - now) / 1000)) };
  }
  return { ok: true };
}

/** Claude Haiku 5.5 list prices (USD per million tokens) for prompts up to 100K tokens, and above. */
export const PRICE = {
  small: { input: 0.10, output: 0.50 },
  large: { input: 0.50, output: 2.50 },
  cacheWrite: 1.25,  // multiple of the input rate, 5-minute cache
  cacheRead: 0.10,   // multiple of the input rate
};

export type Usage = {
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens?: number | null;
  cache_read_input_tokens?: number | null;
};

/** Estimated cost of one answer from the API's usage fields. */
export function costUsd(u: Usage): number {
  const write = u.cache_creation_input_tokens ?? 0, read = u.cache_read_input_tokens ?? 0;
  const prompt = u.input_tokens + write + read;
  const rate = prompt > 100_000 ? PRICE.large : PRICE.small;
  const usd = (u.input_tokens * rate.input + write * rate.input * PRICE.cacheWrite + read * rate.input * PRICE.cacheRead
    + u.output_tokens * rate.output) / 1e6;
  return Math.round(usd * 1e7) / 1e7;
}

/** A visitor key that is not their address: SHA-256 of a secret salt, the UTC day and the IP, cut to 16 hex digits.
 *  It changes every day, so nothing links a visitor across days, and the address itself is never stored. */
export async function visitorKey(ip: string, salt: string, now: number): Promise<string> {
  const bytes = new TextEncoder().encode(`${salt}|${utcDay(now)}|${ip}`);
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return [...hash.slice(0, 8)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
