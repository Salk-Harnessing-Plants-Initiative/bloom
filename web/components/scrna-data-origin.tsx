"use client";

import type { DataOrigin } from "@/lib/scrna-jobs";

const ORIGINS: readonly [DataOrigin, string][] = [
  ["hpi", "HPI"],
  ["public", "Public dataset"],
];

/** Whether the data is HPI's own or public, and a public dataset's source. */
export default function ScrnaDataOrigin({
  origin,
  sourceUrl,
  citation,
  onOrigin,
  onSourceUrl,
  onCitation,
  fieldClass,
  labelClass,
}: {
  origin: DataOrigin;
  sourceUrl: string;
  citation: string;
  onOrigin: (origin: DataOrigin) => void;
  onSourceUrl: (value: string) => void;
  onCitation: (value: string) => void;
  fieldClass: string;
  labelClass: string;
}) {
  return (
    <fieldset>
      <legend className={labelClass}>Where the data is from</legend>
      <div className="mt-1 flex flex-wrap gap-x-6 gap-y-1 text-sm text-stone-700">
        {ORIGINS.map(([value, label]) => (
          <label key={value} className="flex items-center gap-2">
            <input
              type="radio"
              name="data-origin"
              value={value}
              checked={origin === value}
              onChange={() => onOrigin(value)}
              className="accent-lime-700"
            />
            {label}
          </label>
        ))}
      </div>
      {origin === "public" ? (
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <label className={labelClass}>
            Source link
            <input
              type="url"
              className={fieldClass}
              value={sourceUrl}
              onChange={(e) => onSourceUrl(e.target.value)}
              placeholder="e.g. https://doi.org/10.1016/…"
            />
          </label>
          <label className={labelClass}>
            Citation{" "}
            <span className="font-normal text-stone-400">(optional)</span>
            <input
              className={fieldClass}
              value={citation}
              onChange={(e) => onCitation(e.target.value)}
              placeholder="e.g. Shahan et al. 2022"
            />
          </label>
        </div>
      ) : null}
    </fieldset>
  );
}
