// The edge in front of kilde.tahcin.tech. Static files (the site and its data) come straight from Cloudflare's asset
// store; only /api/* runs this code.
//
// POST /api/ask {orgs: [1 to 3 org numbers], question, history?: [{q, a}] up to 4}
//   Loads Kilde's facts for those companies from the asset store, asks Claude Haiku to answer from them only, and
//   streams the answer back as server-sent events: {t:"text", v} pieces, then {t:"done", usage, usd} or {t:"error"}.
//   ?dry_run=1 runs every check and builds the request, but never calls the API and never counts as a question.
// GET /api/status  whether asking is switched on, and whether today's site-wide allowance is left.
//
// Cost guards: a per-visitor limit (10 questions per 10 minutes), a site-wide daily count (600) and a site-wide daily
// spend estimate ($2, from each answer's usage fields), all kept in one Durable Object; 500-character questions;
// 500 output tokens; only org numbers that exist in the published data. Nothing personal is stored or logged: the
// visitor key is a salted daily hash of the address, and questions and answers are never written anywhere.
import Anthropic from "@anthropic-ai/sdk";
import { DurableObject } from "cloudflare:workers";
import { DEFAULT_LIMITS, costUsd, decide, today, utcDay, visitorKey, type DayState, type Limits, type Verdict } from "./guard";
import { buildRequest, parseAsk } from "./prompt";

export interface Env {
  ASSETS: Fetcher;
  GUARD: DurableObjectNamespace<Guard>;
  ANTHROPIC_API_KEY: string;
  IP_SALT: string;
  ASK_ENABLED?: string;
  ALLOWED_ORIGINS?: string;
  LIMIT_PER_IP?: string;
  LIMIT_PER_DAY?: string;
  LIMIT_USD_PER_DAY?: string;
}

function limits(env: Env): Limits {
  const n = (v: string | undefined, d: number) => (v && Number.isFinite(Number(v)) ? Number(v) : d);
  return { ...DEFAULT_LIMITS, perIp: n(env.LIMIT_PER_IP, DEFAULT_LIMITS.perIp), perDay: n(env.LIMIT_PER_DAY, DEFAULT_LIMITS.perDay),
    usdPerDay: n(env.LIMIT_USD_PER_DAY, DEFAULT_LIMITS.usdPerDay) };
}

/** One instance ("global") holds the day's counters and each visitor key's recent question times. */
export class Guard extends DurableObject<Env> {
  sql: SqlStorage;
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec("CREATE TABLE IF NOT EXISTS hits (k TEXT NOT NULL, ts INTEGER NOT NULL)");
    this.sql.exec("CREATE INDEX IF NOT EXISTS hits_k ON hits (k)");
  }

  /** Decide, and when allowed (and not a dry run) count the question at once, so parallel requests can't slip past. */
  async admit(key: string, lim: Limits, dry: boolean): Promise<Verdict> {
    const now = Date.now();
    this.sql.exec("DELETE FROM hits WHERE ts < ?", now - lim.windowMs);
    const hits = this.sql.exec<{ ts: number }>("SELECT ts FROM hits WHERE k = ?", key).toArray().map((r) => r.ts);
    const state = today(await this.ctx.storage.get<DayState>("day"), now);
    const verdict = decide(state, hits, now, lim);
    if (verdict.ok && !dry) {
      this.sql.exec("INSERT INTO hits (k, ts) VALUES (?, ?)", key, now);
      await this.ctx.storage.put("day", { ...state, count: state.count + 1 });
    }
    return verdict;
  }

  async record(usd: number): Promise<void> {
    const state = today(await this.ctx.storage.get<DayState>("day"), Date.now());
    await this.ctx.storage.put("day", { ...state, usd: state.usd + usd });
  }

  async day(): Promise<DayState> {
    return today(await this.ctx.storage.get<DayState>("day"), Date.now());
  }
}

const json = (body: unknown, status = 200, extra: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...extra } });

const MESSAGES = (lim: Limits): Record<string, string> => ({
  ip: `You have asked ${lim.perIp} questions in the last ${Math.round(lim.windowMs / 60_000)} minutes. Please wait a few minutes and ask again.`,
  day: "Today's questions for the whole site are used up. Asking opens again at midnight UTC. Every fact and its source is still here to read.",
  budget: "Today's question budget for the whole site is used up. Asking opens again at midnight UTC. Every fact and its source is still here to read.",
});

/** A data file from the asset store as text. Kilde stores them gzipped; decompress unless the store already did. */
async function asset(env: Env, origin: string, path: string): Promise<string | null> {
  const res = await env.ASSETS.fetch(new Request(origin + path));
  // Unknown paths fall back to the single-page app's HTML, so a 200 alone proves nothing.
  if (!res.ok || (res.headers.get("content-type") ?? "").includes("text/html")) return null;
  const bytes = new Uint8Array(await res.arrayBuffer());
  if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
    return new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"))).text();
  }
  return new TextDecoder().decode(bytes);
}

async function ask(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  const url = new URL(req.url);
  if (env.ASK_ENABLED !== "true") return json({ error: "disabled", message: "Asking is switched off right now. Every fact and its source is still here to read." }, 503);
  const allowed = (env.ALLOWED_ORIGINS ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  const origin = req.headers.get("Origin") ?? "";
  if (!allowed.includes(origin)) return json({ error: "forbidden", message: "Ask from the Kilde website." }, 403);
  if (Number(req.headers.get("content-length") ?? 0) > 16_384) return json({ error: "too_long", message: "That request is too long." }, 413);

  let body: unknown;
  try { body = await req.json(); } catch { return json({ error: "bad_request", message: "Send a JSON body." }, 400); }
  const input = parseAsk(body);
  if ("error" in input) return json(input, 400);

  const facts = await Promise.all(input.orgs.map((o) => asset(env, url.origin, `/data/llm/${o}.txt.gz`)));
  if (facts.some((f) => f === null)) return json({ error: "bad_orgs", message: "One of those companies is not in Kilde's published data." }, 404);
  const request = buildRequest(facts as string[], input);

  const dry = url.searchParams.get("dry_run") === "1";
  const key = await visitorKey(req.headers.get("CF-Connecting-IP") ?? "unknown", env.IP_SALT ?? "", Date.now());
  const guard = env.GUARD.get(env.GUARD.idFromName("global"));
  const lim = limits(env);
  const verdict = await guard.admit(key, lim, dry);
  if (!verdict.ok) {
    return json({ error: verdict.reason, message: MESSAGES(lim)[verdict.reason], retry_after: verdict.retryAfter }, 429,
      { "retry-after": String(verdict.retryAfter) });
  }
  if (dry) {
    return json({ ok: true, dry_run: true, model: request.model, max_tokens: request.max_tokens, orgs: input.orgs,
      system_chars: request.system[0].text.length, facts_chars: facts.reduce((n, f) => n + (f?.length ?? 0), 0),
      messages: request.messages.length });
  }

  const enc = new TextEncoder();
  const { readable, writable } = new TransformStream<Uint8Array, Uint8Array>();
  const writer = writable.getWriter();
  const send = (obj: unknown) => writer.write(enc.encode(`data: ${JSON.stringify(obj)}\n\n`)).catch(() => { /* visitor left */ });

  ctx.waitUntil((async () => {
    try {
      const client = new Anthropic({ apiKey: env.ANTHROPIC_API_KEY, maxRetries: 1, timeout: 45_000 });
      const stream = client.messages.stream(request);
      for await (const ev of stream) {
        if (ev.type === "content_block_delta" && ev.delta.type === "text_delta") await send({ t: "text", v: ev.delta.text });
      }
      const msg = await stream.finalMessage();
      const usd = costUsd(msg.usage);
      await guard.record(usd);
      await send({ t: "done", stop: msg.stop_reason, usd, usage: {
        input: msg.usage.input_tokens, output: msg.usage.output_tokens,
        cache_write: msg.usage.cache_creation_input_tokens ?? 0, cache_read: msg.usage.cache_read_input_tokens ?? 0 } });
    } catch (err) {
      const busy = err instanceof Anthropic.RateLimitError || (err instanceof Anthropic.APIError && (err.status ?? 0) >= 500);
      console.error("ask failed:", err instanceof Anthropic.APIError ? `API ${err.status}` : (err as Error)?.name);
      await send({ t: "error", error: busy ? "busy" : "upstream",
        message: busy ? "Claude is busy right now. Please try again in a minute." : "The answer could not be written this time. Please try again." });
    } finally {
      await writer.close().catch(() => { /* already closed */ });
    }
  })());

  return new Response(readable, { headers: { "content-type": "text/event-stream; charset=utf-8", "cache-control": "no-store", "x-accel-buffering": "no" } });
}

async function status(env: Env): Promise<Response> {
  if (env.ASK_ENABLED !== "true") return json({ enabled: false, open: false });
  const lim = limits(env);
  const day = await env.GUARD.get(env.GUARD.idFromName("global")).day();
  return json({ enabled: true, open: day.count < lim.perDay && day.usd < lim.usdPerDay, day: utcDay(Date.now()) });
}

export default {
  async fetch(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const { pathname } = new URL(req.url);
    if (pathname === "/api/ask") return req.method === "POST" ? ask(req, env, ctx) : json({ error: "method" }, 405, { allow: "POST" });
    if (pathname === "/api/status") return status(env);
    if (pathname.startsWith("/api/")) return json({ error: "not_found" }, 404);
    return env.ASSETS.fetch(req);
  },
} satisfies ExportedHandler<Env>;
