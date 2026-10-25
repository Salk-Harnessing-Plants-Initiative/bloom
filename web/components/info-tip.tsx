/**
 * Bloom's info icon: a green circled "i" (dark green on light green) that opens a note on
 * hover or keyboard focus. Use it for every ⓘ in the web app, so they all look and behave
 * the same.
 *
 * It's CSS only (no state), so it works in a server component. It is focusable itself, so
 * don't put it inside a <button>. `label` is what screen readers announce; `children` is
 * the note.
 */

import type { ReactNode } from "react";

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
  return (
    <span className="group/info relative ml-1 inline-block align-middle">
      <span
        tabIndex={0}
        role="img"
        aria-label={label}
        className="flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-lime-700 bg-lime-50 text-[10px] font-semibold leading-none text-lime-800 outline-none focus:ring-1 focus:ring-lime-600"
      >
        i
      </span>
      <span
        role="tooltip"
        className={`pointer-events-none absolute top-6 z-20 block w-72 rounded-md border border-lime-600 bg-lime-50 px-3 py-2 text-sm font-normal not-italic leading-snug text-stone-700 opacity-0 shadow-md transition-opacity group-hover/info:opacity-100 group-focus-within/info:opacity-100 ${
          align === "right" ? "right-0" : "left-0"
        }`}
      >
        {children}
      </span>
    </span>
  );
}
