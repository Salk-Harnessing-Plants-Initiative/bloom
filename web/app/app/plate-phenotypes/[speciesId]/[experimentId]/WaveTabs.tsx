"use client";

import { useState, type ReactNode } from "react";

// Thin client-side tab switcher: the per-wave plate lists are rendered on the
// server and passed in as panels, so all the heavy plate UI stays server-side.
export function WaveTabs({
  labels,
  panels,
}: {
  labels: string[];
  panels: ReactNode[];
}) {
  const [active, setActive] = useState(0);

  return (
    <div>
      <div role="tablist" className="mb-5 flex flex-wrap gap-2 select-none">
        {labels.map((label, i) => (
          <button
            key={label}
            type="button"
            role="tab"
            aria-selected={i === active}
            onClick={() => setActive(i)}
            className={
              i === active
                ? "rounded-md border border-lime-700 bg-lime-50 px-3 py-1 text-sm font-medium text-lime-800"
                : "rounded-md border border-stone-300 px-3 py-1 text-sm font-medium text-stone-600 hover:border-lime-700 hover:text-lime-800"
            }
          >
            {label}
          </button>
        ))}
      </div>
      <div role="tabpanel">{panels[active]}</div>
    </div>
  );
}
