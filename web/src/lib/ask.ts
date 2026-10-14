// The browser side of /api/ask: send a question, read the streamed answer.
export type Turn = { q: string; a: string };
export type AskDone = { usd?: number; stop?: string; usage?: { input: number; output: number; cache_write: number; cache_read: number } };
export class AskError extends Error {
  code: string;
  constructor(code: string, message: string) { super(message); this.code = code; }
}

export async function ask(orgs: string[], question: string, history: Turn[], onText: (t: string) => void, signal?: AbortSignal): Promise<AskDone> {
  let res: Response;
  try {
    res = await fetch("/api/ask", {
      method: "POST", headers: { "content-type": "application/json" }, signal,
      body: JSON.stringify({ orgs, question, history: history.slice(-4) }),
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new AskError("network", "Could not reach Kilde. Check your connection and try again.");
  }
  if (!res.ok || !(res.headers.get("content-type") ?? "").includes("event-stream")) {
    const body = await res.json().catch(() => ({}));
    throw new AskError(body.error ?? "error", body.message ?? "Something went wrong. Please try again.");
  }
  const reader = res.body!.getReader();
  const dec = new TextDecoder();
  let buf = "", done: AskDone | null = null;
  for (;;) {
    const { value, done: end } = await reader.read();
    if (end) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const ev = JSON.parse(line.slice(6));
      if (ev.t === "text") onText(ev.v);
      else if (ev.t === "done") done = ev;
      else if (ev.t === "error") throw new AskError(ev.error, ev.message);
    }
  }
  if (!done) throw new AskError("cut", "The answer was cut off. Please try again.");
  return done;
}
