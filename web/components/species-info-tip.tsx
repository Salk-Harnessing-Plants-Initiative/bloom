"use client";

/**
 * The ⓘ next to the Species page's description: hovering or clicking it says when to add
 * a species here. Species are matched by name when Bloom Desktop uploads cylinder and
 * plate scans, and an Expression analysis is run for one, so it has to exist first.
 */

import { useState } from "react";

export default function SpeciesInfoTip() {
  const [open, setOpen] = useState(false);
  return (
    <span
      className="relative ml-1 inline-block align-middle"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label="When to add a species"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        onBlur={() => setOpen(false)}
        className="flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-stone-400 text-[10px] font-semibold leading-none text-stone-500 hover:border-lime-700 hover:text-lime-700"
      >
        i
      </button>
      {open && (
        <span
          role="tooltip"
          className="absolute left-0 top-6 z-20 block w-72 rounded-md border border-lime-600 bg-lime-50 p-3 text-sm leading-snug text-stone-700 shadow-md"
        >
          <span className="block font-medium text-stone-800">
            Add a species here before you use it in:
          </span>
          <span className="mt-1 block">• Bloom Desktop, for cylinder or plate scans</span>
          <span className="block">• an Expression analysis</span>
        </span>
      )}
    </span>
  );
}
