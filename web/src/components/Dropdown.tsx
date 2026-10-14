// The one dropdown used everywhere instead of a native <select>: single choice (sort) or several (filters), with an
// optional search for long lists. Keyboard: arrows to move, Enter to pick, typing jumps to a match, Esc closes.
import * as Popover from "@radix-ui/react-popover";
import { Command } from "cmdk";
import { Check, ChevronDown, Search, X } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { cn } from "../lib/format";

export type Option = { value: string; label: string; count?: number };

type Props = {
  label: string;
  options: Option[];
  searchable?: boolean;
  align?: "start" | "end";
  className?: string;
} & ({ multiple?: false; value: string; onChange: (v: string) => void } | { multiple: true; value: string[]; onChange: (v: string[]) => void });

export function Dropdown(props: Props) {
  const { label, options, searchable, align = "start", className } = props;
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState<string>("");
  const typed = useRef({ s: "", t: 0 });
  const selected = props.multiple ? props.value : [props.value];
  const current = !props.multiple ? options.find((o) => o.value === props.value) : null;
  const shown = useMemo(() => {
    const f = q.trim().toLowerCase();
    return f ? options.filter((o) => o.label.toLowerCase().includes(f)) : options;
  }, [options, q]);

  const pick = (v: string) => {
    if (props.multiple) props.onChange(props.value.includes(v) ? props.value.filter((x) => x !== v) : [...props.value, v]);
    else { props.onChange(v); setOpen(false); }
  };
  // Type-ahead when there is no search box: letters jump to the first option starting with them.
  const onKey = (e: React.KeyboardEvent) => {
    if (searchable || e.key.length !== 1 || e.metaKey || e.ctrlKey) return;
    const now = Date.now();
    typed.current = { s: (now - typed.current.t < 700 ? typed.current.s : "") + e.key.toLowerCase(), t: now };
    const hit = options.find((o) => o.label.toLowerCase().startsWith(typed.current.s));
    if (hit) setActive(hit.value);
  };

  const n = props.multiple ? props.value.length : 0;
  return (
    <Popover.Root open={open} onOpenChange={(v) => { setOpen(v); if (v) { setQ(""); setActive(selected[0] ?? options[0]?.value ?? ""); } }}>
      <Popover.Trigger
        className={cn("group flex h-9 items-center gap-2 rounded-xl border bg-card pl-3 pr-2.5 text-[13.5px] shadow-[0_1px_2px_rgb(28_38_43/0.04)] transition",
          n > 0 ? "border-brand/35 bg-wash text-brand" : "border-rule text-ink-2 hover:border-brand/35 data-[state=open]:border-brand/45", className)}>
        <span className={cn(!props.multiple && "text-muted")}>{label}</span>
        {current && <span className="font-medium text-ink">{current.label}</span>}
        {n > 0 && <span className="grid h-5 min-w-5 place-items-center rounded-full bg-brand px-1 text-[11px] font-semibold text-on">{n}</span>}
        <ChevronDown className="size-4 text-muted transition-transform duration-200 group-data-[state=open]:rotate-180" aria-hidden />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align={align} sideOffset={6} collisionPadding={12}
          className="z-50 w-[min(19rem,calc(100vw-24px))] overflow-hidden rounded-2xl border border-rule bg-card shadow-[var(--shadow-float)] outline-none transition-[opacity,transform] duration-200 ease-[var(--ease-out-expo)] starting:-translate-y-1 starting:opacity-0">
          <Command label={label} shouldFilter={false} loop value={active} onValueChange={setActive} onKeyDown={onKey}>
            {searchable && (
              <div className="flex items-center gap-2 border-b border-rule/80 px-3">
                <Search className="size-4 shrink-0 text-muted" aria-hidden />
                <Command.Input autoFocus value={q} onValueChange={setQ} placeholder={`Search ${label.toLowerCase()}`}
                  className="h-10 min-w-0 flex-1 bg-transparent text-[14px] text-ink outline-none placeholder:text-muted" />
              </div>
            )}
            {/* Without a search box the list itself takes focus, so arrows and type-ahead work at once. */}
            <Command.List tabIndex={searchable ? undefined : 0} autoFocus={!searchable}
              className="max-h-[min(50vh,320px)] overflow-y-auto overscroll-contain p-1.5 outline-none scroll-thin">
              <Command.Empty className="px-3 py-4 text-center text-[13px] text-muted">Nothing matches.</Command.Empty>
              {shown.map((o) => {
                const on = selected.includes(o.value);
                return (
                  <Command.Item key={o.value} value={o.value} onSelect={() => pick(o.value)}
                    className="flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2 text-[14px] text-ink-2 data-[selected=true]:bg-hover data-[selected=true]:text-ink">
                    <span className={cn("grid size-4 shrink-0 place-items-center", props.multiple && "rounded-[5px] border", props.multiple && (on ? "border-brand bg-brand text-on" : "border-rule"))}>
                      {on && <Check className={cn("size-3.5", !props.multiple && "text-brand")} strokeWidth={2.6} aria-hidden />}
                    </span>
                    <span className={cn("min-w-0 flex-1 truncate", on && "font-medium text-ink")}>{o.label}</span>
                    {o.count != null && <span className="text-[12px] text-muted">{o.count}</span>}
                  </Command.Item>
                );
              })}
            </Command.List>
            {props.multiple && (
              <div className="flex items-center justify-between border-t border-rule/80 px-3 py-2 text-[12.5px]">
                <span className="text-muted">{n ? `${n} selected` : "Pick any"}</span>
                <span className="flex gap-1">
                  {n > 0 && <button onClick={() => props.onChange([])} className="flex items-center gap-1 rounded-lg px-2 py-1 text-muted hover:bg-hover hover:text-ink"><X className="size-3.5" aria-hidden />Clear</button>}
                  <button onClick={() => setOpen(false)} className="rounded-lg bg-brand px-2.5 py-1 font-medium text-on hover:bg-brand-2">Done</button>
                </span>
              </div>
            )}
          </Command>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
