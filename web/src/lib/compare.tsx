// The companies picked to compare (up to three), kept in this browser between visits.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

type Ctx = { picked: string[]; toggle: (o: string) => boolean; remove: (o: string) => void; clear: () => void; set: (os: string[]) => void };
const C = createContext<Ctx | null>(null);
export const MAX_PICK = 3;
const KEY = "kilde.compare";

function read(): string[] {
  try { const v = JSON.parse(localStorage.getItem(KEY) ?? "[]"); return Array.isArray(v) ? v.filter((o) => /^\d{9}$/.test(o)).slice(0, MAX_PICK) : []; }
  catch { return []; }
}

export function CompareProvider({ children }: { children: ReactNode }) {
  const [picked, setPicked] = useState<string[]>(read);
  useEffect(() => { try { localStorage.setItem(KEY, JSON.stringify(picked)); } catch { /* storage blocked */ } }, [picked]);
  /** Adds or removes; returns false when the list is full. */
  const toggle = useCallback((o: string) => {
    if (picked.includes(o)) { setPicked(picked.filter((x) => x !== o)); return true; }
    if (picked.length >= MAX_PICK) return false;
    setPicked([...picked, o]);
    return true;
  }, [picked]);
  const value = useMemo<Ctx>(() => ({
    picked, toggle, remove: (o) => setPicked((p) => p.filter((x) => x !== o)), clear: () => setPicked([]),
    set: (os) => setPicked([...new Set(os)].filter((o) => /^\d{9}$/.test(o)).slice(0, MAX_PICK)),
  }), [picked, toggle]);
  return <C.Provider value={value}>{children}</C.Provider>;
}

export function useCompare(): Ctx {
  const c = useContext(C);
  if (!c) throw new Error("CompareProvider missing");
  return c;
}
