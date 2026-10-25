"use client";

/**
 * Bloom's info icon: a green circled "i" (dark green on light green) that opens a note on
 * hover or keyboard focus. Use it for every ⓘ in the web app, so they all look and behave
 * the same.
 *
 * The note stays open while the pointer moves onto it, closes on Esc, and is the icon's
 * description for screen readers (hidden from them while closed). It is focusable itself,
 * so don't put it inside a <button>. `label` is what screen readers announce for the
 * icon; `children` is the note.
 */

import { useId, useState, type ReactNode } from "react";

export default function InfoTip({
  label,
  children,
  align = "left",
}: {
  label: string;
  children: ReactNode;
  // Which edge of the icon the note lines up with; "right" for an icon near the right edge.
  align?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const noteId = useId();
  return (
    <span
      className="relative ml-1 inline-flex align-middle"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <span
        tabIndex={0}
        role="img"
        aria-label={label}
        aria-describedby={noteId}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
        }}
        className="flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-lime-700 bg-lime-50 text-[10px] font-semibold leading-none text-lime-800 outline-none focus:ring-1 focus:ring-lime-600"
      >
        i
      </span>
      {/* The padding bridges the gap to the icon, so the note stays open on the way to it. */}
      <span
        id={noteId}
        role="tooltip"
        hidden={!open}
        className={`absolute top-full z-20 w-72 pt-1.5 ${align === "right" ? "right-0" : "left-0"}`}
      >
        <span className="block rounded-md border border-lime-600 bg-lime-50 px-3 py-2 text-sm font-normal not-italic leading-snug text-stone-700 shadow-md">
          {children}
        </span>
      </span>
    </span>
  );
}
