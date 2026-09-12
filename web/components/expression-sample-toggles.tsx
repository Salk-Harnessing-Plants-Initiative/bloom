"use client";

/**
 * Show or hide the cells of each value in one filter row: the samples, or one of
 * the labels the cells carry, such as transgene status. Each value can also be
 * focused on, which greys out every cell that does not have it.
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
  /** Values focused on; cells without them are greyed out. */
  focused?: ReadonlySet<string>;
  /** Focuses on or stops focusing on one value; without it no focus buttons show. */
  onFocus?: (name: string) => void;
}

export function ExpressionSampleToggles({
  label = "Samples",
  noun = "sample",
  samples,
  hidden,
  unlabelledCount,
  onToggle,
  onShowAll,
  focused,
  onFocus,
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
        const isFocused = focused?.has(sample.name) ?? false;
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
            {onFocus && (
              <button
                type="button"
                onClick={() => onFocus(sample.name)}
                aria-pressed={isFocused}
                aria-label={`Focus on ${sample.name}`}
                title={
                  isFocused
                    ? `Stop focusing on ${sample.name}`
                    : `Focus on ${sample.name}: grey out every other cell`
                }
                className={`rounded-md border px-1.5 py-1 text-xs leading-none transition-colors ${
                  isFocused
                    ? "border-stone-700 bg-stone-700 text-white"
                    : "border-stone-200 bg-white text-stone-400 hover:border-stone-400 hover:text-stone-700"
                }`}
              >
                ◎
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
