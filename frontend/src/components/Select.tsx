"use client";

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import clsx from "./clsx";

export type Option = { value: string; label: string; hint?: string; disabled?: boolean };

type Props = {
  value: string;
  onChange: (value: string) => void;
  options: Option[];
  placeholder?: string;
  id?: string;
  ariaLabel?: string;
  className?: string;
  size?: "sm" | "md";
  /** Menu width: match the button (default) or grow to fit the longest label. */
  menuWidth?: "button" | "content";
  disabled?: boolean;
};

/**
 * A dropdown that looks like the rest of the dashboard instead of the browser's own list.
 * The menu is drawn in a portal so a scrolling board column or a card can never clip it, flips upward near the bottom of the screen,
 * and works from the keyboard (arrows, Home/End, Enter, Escape, type a letter to jump).
 */
export default function Select({ value, onChange, options, placeholder = "Select...", id, ariaLabel, className, size = "md", menuWidth = "button", disabled }: Props) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [pos, setPos] = useState<{ left: number; top?: number; bottom?: number; width: number; maxHeight: number } | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLUListElement>(null);
  const listId = useId();
  const selected = options.find((o) => o.value === value);

  const place = useCallback(() => {
    const el = button.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const below = window.innerHeight - r.bottom - 12;
    const above = r.top - 12;
    const flip = below < 200 && above > below;
    const width = Math.max(r.width, menuWidth === "content" ? 220 : r.width);
    const left = Math.min(Math.max(8, r.left), window.innerWidth - width - 8);
    setPos(flip ? { left, bottom: window.innerHeight - r.top + 6, width, maxHeight: Math.min(320, above) } : { left, top: r.bottom + 6, width, maxHeight: Math.min(320, below) });
  }, [menuWidth]);

  const close = useCallback((refocus = true) => {
    setOpen(false);
    if (refocus) button.current?.focus();
  }, []);

  const show = () => {
    if (disabled) return;
    place();
    setActive(Math.max(0, options.findIndex((o) => o.value === value)));
    setOpen(true);
  };

  useLayoutEffect(() => {
    if (open) menu.current?.querySelector<HTMLElement>('[data-active="true"]')?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!menu.current?.contains(t) && !button.current?.contains(t)) close(false);
    };
    const move = (e: Event) => { if (!menu.current?.contains(e.target as Node)) place(); };
    document.addEventListener("pointerdown", away);
    window.addEventListener("resize", move);
    window.addEventListener("scroll", move, true);
    return () => { document.removeEventListener("pointerdown", away); window.removeEventListener("resize", move); window.removeEventListener("scroll", move, true); };
  }, [open, close, place]);

  const choose = (o: Option) => {
    if (o.disabled) return;
    onChange(o.value);
    close();
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); show(); }
      return;
    }
    const step = (from: number, dir: 1 | -1) => {
      for (let i = 1; i <= options.length; i++) {
        const n = (from + dir * i + options.length) % options.length;
        if (!options[n].disabled) return n;
      }
      return from;
    };
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => step(a, 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => step(a, -1)); }
    else if (e.key === "Home") { e.preventDefault(); setActive(step(-1, 1)); }
    else if (e.key === "End") { e.preventDefault(); setActive(step(options.length, -1)); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (options[active]) choose(options[active]); }
    else if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "Tab") close(false);
    else if (e.key.length === 1) {
      const n = options.findIndex((o, i) => i > active && o.label.toLowerCase().startsWith(e.key.toLowerCase()));
      const m = n >= 0 ? n : options.findIndex((o) => o.label.toLowerCase().startsWith(e.key.toLowerCase()));
      if (m >= 0) setActive(m);
    }
  };

  return (
    <>
      <button
        ref={button} id={id} type="button" role="combobox" aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? listId : undefined} aria-label={ariaLabel}
        disabled={disabled} onClick={() => (open ? close(false) : show())} onKeyDown={onKey}
        className={clsx(
          "flex w-full items-center justify-between gap-2 rounded-lg border border-line bg-[#0d0d24] text-left transition-colors hover:border-[#3a3a62] focus:border-accent focus:outline-none focus:ring-[3px] focus:ring-accent/20 disabled:cursor-not-allowed disabled:opacity-50",
          size === "sm" ? "px-2.5 py-1.5 text-xs" : "px-3 py-2 text-sm", open && "border-accent", className,
        )}
      >
        <span className={clsx("truncate", !selected && "text-muted")}>{selected?.label ?? placeholder}</span>
        <svg viewBox="0 0 20 20" className={clsx("h-4 w-4 shrink-0 text-muted transition-transform", open && "rotate-180")} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m5 8 5 5 5-5" /></svg>
      </button>
      {open && pos && typeof document !== "undefined" && createPortal(
        <ul
          ref={menu} id={listId} role="listbox" aria-label={ariaLabel}
          style={{ position: "fixed", left: pos.left, top: pos.top, bottom: pos.bottom, minWidth: pos.width, maxHeight: pos.maxHeight }}
          className="z-[60] overflow-y-auto rounded-xl border border-[#2e2e52] bg-[#12122a] p-1 shadow-2xl shadow-black/60"
        >
          {options.map((o, i) => (
            <li
              key={o.value} role="option" aria-selected={o.value === value} aria-disabled={o.disabled} data-active={i === active}
              onPointerEnter={() => setActive(i)} onClick={() => choose(o)}
              className={clsx("flex cursor-pointer items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-sm", i === active ? "bg-white/[0.07] text-white" : "text-ink/90", o.disabled && "cursor-not-allowed opacity-40")}
            >
              <span className="min-w-0">
                <span className="block truncate">{o.label}</span>
                {o.hint && <span className="block truncate text-xs text-muted">{o.hint}</span>}
              </span>
              {o.value === value && <svg viewBox="0 0 20 20" className="h-4 w-4 shrink-0 text-accent" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m4.5 10.5 3.5 3.5 7.5-8" /></svg>}
            </li>
          ))}
        </ul>,
        document.body,
      )}
    </>
  );
}
