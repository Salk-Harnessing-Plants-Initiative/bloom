"use client";

import type { AttributeRow } from "@/lib/scrna-jobs";

const EMPTY_ROW: AttributeRow = { key: "", value: "" };

/** Free name/value rows about the sample, such as tissue or days after germination. */
export default function ScrnaOtherDetails({
  rows,
  onChange,
  fieldClass,
  labelClass,
}: {
  rows: AttributeRow[];
  onChange: (rows: AttributeRow[]) => void;
  fieldClass: string;
  labelClass: string;
}) {
  function update(index: number, change: Partial<AttributeRow>) {
    onChange(rows.map((row, i) => (i === index ? { ...row, ...change } : row)));
  }

  // The last row is emptied rather than removed, so there's always one to type in.
  function remove(index: number) {
    onChange(rows.length === 1 ? [EMPTY_ROW] : rows.filter((_, i) => i !== index));
  }

  return (
    <div>
      <div className={labelClass}>
        Other details <span className="font-normal text-stone-400">(optional)</span>
      </div>
      <p className="mt-0.5 text-sm text-stone-500">
        Anything else about the sample, such as tissue, days after germination,
        treatment or replicate.
      </p>
      <ul className="mt-2 space-y-2">
        {rows.map((row, index) => (
          <li key={index} className="flex items-center gap-2">
            <input
              aria-label={`Field ${index + 1} name`}
              className={`${fieldClass} mt-0`}
              value={row.key}
              onChange={(e) => update(index, { key: e.target.value })}
              placeholder="Name, e.g. tissue"
            />
            <input
              aria-label={`Field ${index + 1} value`}
              className={`${fieldClass} mt-0`}
              value={row.value}
              onChange={(e) => update(index, { value: e.target.value })}
              placeholder="Value, e.g. root"
            />
            <button
              type="button"
              aria-label={`Remove field ${index + 1}`}
              onClick={() => remove(index)}
              className="shrink-0 px-2 text-lg text-stone-400 hover:text-stone-700"
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <button
        type="button"
        onClick={() => onChange([...rows, EMPTY_ROW])}
        className="mt-2 text-sm text-lime-700 hover:underline underline-offset-4"
      >
        + Add field
      </button>
    </div>
  );
}
