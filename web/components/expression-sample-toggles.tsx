"use client";

/**
 * Show or hide the cells of each value in one filter row: the samples, or one of
 * the labels the cells carry, such as transgene status. Each value can also be
 * highlighted, which draws its cells in yellow on top of the rest of the map.
 *
 * The values come from the cells themselves, so a dataset with different ones
 * — or none at all — needs no change here. A row with no values renders
 * nothing, rather than an empty box.
 */

export interface SampleCount {
  name: string;
  count: number;
}

interface Props {
  /** What the row filters on: "Samples", or a label's own name. */
  label?: string;
  /** One of the row's values, in words: "sample", or "transgene_pos value". */
  noun?: string;
  samples: SampleCount[];
  hidden: ReadonlySet<string>;
  /** Cells with no value in this row. They stay on the map whatever the row
   *  hides, so hiding every value does not empty it. Required, so dropping the
   *  wire is a compile error rather than a quietly wrong message. */
  unlabelledCount: number;
  onToggle: (name: string) => void;
  onShowAll: () => void;
  /** Values whose cells are drawn in yellow. */
  highlighted?: ReadonlySet<string>;
  /** Highlights or stops highlighting one value; without it no highlight buttons show. */
  onHighlight?: (name: string) => void;
}

export function ExpressionSampleToggles({
  label = "Samples",
  noun = "sample",
  samples,
  hidden,
  unlabelledCount,
  onToggle,
  onShowAll,
  highlighted,
  onHighlight,
}: Props) {
  if (samples.length === 0) return null;

  const allHidden = samples.every((s) => hidden.has(s.name));
  const fmt = new Intl.NumberFormat("en-US");

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-[10px] uppercase tracking-widest text-stone-500">
        {label}
      </span>
      {samples.map((sample) => {
        const isHidden = hidden.has(sample.name);
        const isHighlighted = highlighted?.has(sample.name) ?? false;
        return (
          <span key={sample.name} className="inline-flex items-center gap-0.5">
            <button
              type="button"
              onClick={() => onToggle(sample.name)}
              aria-pressed={!isHidden}
              title={
                isHidden
                  ? `Show ${sample.name}`
                  : `Hide ${sample.name}`
              }
              className={`inline-flex items-baseline gap-1.5 rounded-md border px-2.5 py-1 text-xs transition-colors ${
                isHidden
                  ? "border-stone-200 bg-stone-50 text-stone-500 line-through decoration-stone-400"
                  : "border-lime-300 bg-lime-50 text-stone-700 hover:border-lime-400"
              }`}
            >
              <span className="max-w-[18ch] truncate font-medium">
                {sample.name}
              </span>
              <span className="tabular-nums text-[10px] text-stone-500">
                {fmt.format(sample.count)}
              </span>
            </button>
            {onHighlight && (
              <button
                type="button"
                onClick={() => onHighlight(sample.name)}
                aria-pressed={isHighlighted}
                aria-label={`Highlight ${sample.name}`}
                title={
                  isHighlighted
                    ? `Stop highlighting ${sample.name}`
                    : `Highlight ${sample.name} in yellow`
                }
                className={`rounded-md border px-1.5 py-1 text-xs leading-none transition-colors ${
                  isHighlighted
                    ? "border-yellow-400 bg-yellow-300 text-stone-800"
                    : "border-stone-200 bg-white text-stone-400 hover:border-yellow-300 hover:text-yellow-600"
                }`}
              >
                ✦
              </button>
            )}
          </span>
        );
      })}
      {allHidden && (
        <span className="text-xs text-stone-500">
          {unlabelledCount > 0
            ? `Every ${noun} is hidden. ${fmt.format(unlabelledCount)} cell${
                unlabelledCount === 1 ? "" : "s"
              } record${unlabelledCount === 1 ? "s" : ""} no ${noun} and stay${
                unlabelledCount === 1 ? "s" : ""
              } on the map.`
            : `Every ${noun} is hidden, so the map is empty.`}{" "}
          <button
            type="button"
            onClick={onShowAll}
            className="underline hover:text-stone-700"
          >
            Show all {noun}s
          </button>
        </span>
      )}
    </div>
  );
}
