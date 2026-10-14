// node --test worker/*.test.ts  (Node 23+ runs TypeScript directly)
import { test } from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_LIMITS, costUsd, decide, today, untilMidnight, visitorKey } from "./guard.ts";

const NOON = Date.UTC(2026, 9, 14, 12, 0, 0);
const fresh = today(undefined, NOON);

test("a new visitor on a new day may ask", () => {
  assert.deepEqual(decide(fresh, [], NOON), { ok: true });
});

test("the 11th question inside ten minutes waits until the oldest one leaves the window", () => {
  const hits = Array.from({ length: 10 }, (_, i) => NOON - 9 * 60_000 + i * 1000);  // 10 questions in the last 9 minutes
  const v = decide(fresh, hits, NOON);
  assert.equal(v.ok, false);
  if (!v.ok) { assert.equal(v.reason, "ip"); assert.equal(v.retryAfter, 60); }
  // Questions older than the window do not count.
  assert.deepEqual(decide(fresh, hits.map((t) => t - 2 * 60_000), NOON), { ok: true });
});

test("the site-wide daily count closes asking until midnight UTC", () => {
  const v = decide({ ...fresh, count: DEFAULT_LIMITS.perDay }, [], NOON);
  assert.equal(v.ok, false);
  if (!v.ok) { assert.equal(v.reason, "day"); assert.equal(v.retryAfter, 12 * 3600); }
  assert.deepEqual(decide({ ...fresh, count: DEFAULT_LIMITS.perDay - 1 }, [], NOON), { ok: true });
});

test("the daily spend cap wins over everything else", () => {
  const hits = Array.from({ length: 20 }, () => NOON - 1000);
  const v = decide({ ...fresh, count: 9999, usd: 2.0001 }, hits, NOON);
  assert.equal(v.ok, false);
  if (!v.ok) assert.equal(v.reason, "budget");
});

test("counters reset when the UTC day turns", () => {
  const yesterday = { day: "2026-10-13", count: 600, usd: 5 };
  assert.deepEqual(today(yesterday, NOON), { day: "2026-10-14", count: 0, usd: 0 });
  assert.equal(today(fresh, NOON), fresh);
  assert.equal(untilMidnight(Date.UTC(2026, 9, 14, 23, 59, 30)), 30);
});

test("cost comes from the usage fields at Haiku 5.5 prices", () => {
  // Per million: input $0.10, output $0.50, cache write 1.25x input, cache read 0.1x input (prompts up to 100K).
  assert.equal(costUsd({ input_tokens: 100_000, output_tokens: 0 }), 0.01);
  assert.equal(costUsd({ input_tokens: 0, output_tokens: 1_000_000 }), 0.5);
  assert.equal(costUsd({ input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 100_000 }), 0.0125);
  assert.equal(costUsd({ input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 100_000 }), 0.001);
  // A typical first question on a large company: 7,000 tokens written to cache, 30 plain, 250 out.
  assert.ok(Math.abs(costUsd({ input_tokens: 30, output_tokens: 250, cache_creation_input_tokens: 7000 }) - 0.001003) < 1e-6);
  // Prompts over 100K tokens use the higher rate card.
  assert.equal(costUsd({ input_tokens: 200_000, output_tokens: 0 }), 0.1);
});

test("visitor keys hide the address and change every day", async () => {
  const a = await visitorKey("203.0.113.7", "salt", NOON);
  assert.match(a, /^[0-9a-f]{16}$/);
  assert.ok(!a.includes("203"));
  assert.equal(a, await visitorKey("203.0.113.7", "salt", NOON + 3600_000));
  assert.notEqual(a, await visitorKey("203.0.113.7", "salt", NOON + 86_400_000));
  assert.notEqual(a, await visitorKey("203.0.113.8", "salt", NOON));
});
