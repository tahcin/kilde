import { test } from "node:test";
import assert from "node:assert/strict";
import { MAX_QUESTION, MODEL, SYSTEM, buildRequest, parseAsk } from "./prompt.ts";

test("valid input is cleaned and org numbers sorted, so the cache key does not depend on order", () => {
  const r = parseAsk({ orgs: ["999296432", "938702675", "999296432"], question: "  Who runs it? " });
  assert.deepEqual(r, { orgs: ["938702675", "999296432"], question: "Who runs it?", history: [] });
});

test("bad input is refused with a reason", () => {
  assert.equal((parseAsk({ orgs: [], question: "x" }) as { error: string }).error, "bad_orgs");
  assert.equal((parseAsk({ orgs: ["1", "2", "3", "4"], question: "x" }) as { error: string }).error, "bad_orgs");
  assert.equal((parseAsk({ orgs: ["12345678a"], question: "x" }) as { error: string }).error, "bad_orgs");
  assert.equal((parseAsk({ orgs: ["999296432", "938702675", "979543883", "976605713"], question: "x" }) as { error: string }).error, "bad_orgs");
  assert.equal((parseAsk({ orgs: ["999296432"], question: "  " }) as { error: string }).error, "bad_question");
  assert.equal((parseAsk({ orgs: ["999296432"], question: "x".repeat(MAX_QUESTION + 1) }) as { error: string }).error, "too_long");
  const turns = Array.from({ length: 5 }, () => ({ q: "a", a: "b" }));
  assert.equal((parseAsk({ orgs: ["999296432"], question: "x", history: turns }) as { error: string }).error, "too_long");
  assert.equal((parseAsk({ orgs: ["999296432"], question: "x", history: [{ q: "a" }] }) as { error: string }).error, "bad_history");
  assert.equal((parseAsk(null) as { error: string }).error, "bad_request");
});

test("the request: Haiku, short, no temperature, cached system prompt and facts first", () => {
  const input = parseAsk({ orgs: ["999296432"], question: "Is it hiring?", history: [{ q: "Who runs it?", a: "Sonja Horn. [999296432:c81]" }] });
  if ("error" in input) throw new Error();
  const body = buildRequest(["COMPANY 999296432: ENTRA ASA\n[999296432:c81] General manager: Sonja Horn"], input);
  assert.equal(body.model, MODEL);
  assert.equal(body.model, "claude-haiku-5-5");
  assert.equal(body.max_tokens, 500);
  assert.ok(!("temperature" in body));
  assert.equal(body.system[0].cache_control.type, "ephemeral");
  assert.equal(body.messages.length, 3);
  const first = body.messages[0].content as { text: string; cache_control?: unknown }[];
  assert.match(first[0].text, /\[999296432:c81\]/);
  assert.deepEqual(first[0].cache_control, { type: "ephemeral" });
  assert.equal(first[1].text, "Question: Who runs it?");
  assert.deepEqual(body.messages[1], { role: "assistant", content: "Sonja Horn. [999296432:c81]" });
  assert.deepEqual(body.messages[2], { role: "user", content: "Question: Is it hiring?" });
});

test("the system prompt is frozen text with no em dash and no per-request data", () => {
  assert.ok(!SYSTEM.includes("\u2014"));
  assert.ok(!/20\d\d-\d\d-\d\d/.test(SYSTEM));
});
