"use client";

/**
 * Show or hide each sample on the map.
 *
 * The samples come from the cells themselves, so a dataset with different ones
 * — or none at all — needs no change here. A dataset whose cells record no
 * sample gets no control, rather than an empty box.
 */

export interface SampleCount {
  name: string;
  count: number;
}

interface Props {
  samples: SampleCount[];
  hidden: ReadonlySet<string>;
  /** Cells recording no sample. They stay on the map whatever is hidden, so
   *  hiding every sample does not empty it. Required, so dropping the wire is
   *  a compile error rather than a quietly wrong message. */
  unlabelledCount: number;
  onToggle: (name: string) => void;
  onShowAll: () => void;
}

export function ExpressionSampleToggles({
  samples,
  hidden,
  unlabelledCount,
  onToggle,
  onShowAll,
}: Props) {
  if (samples.length === 0) return null;

  const allHidden = samples.every((s) => hidden.has(s.name));
  const fmt = new Intl.NumberFormat("en-US");

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-[10px] uppercase tracking-widest text-stone-500">
        Samples
      </span>
      {samples.map((sample) => {
        const isHidden = hidden.has(sample.name);
        return (
          <button
            key={sample.name}
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
        );
      })}
      {allHidden && (
        <span className="text-xs text-stone-500">
          {unlabelledCount > 0
            ? `Every sample is hidden. ${fmt.format(unlabelledCount)} cell${
                unlabelledCount === 1 ? "" : "s"
              } record${unlabelledCount === 1 ? "s" : ""} no sample and stay${
                unlabelledCount === 1 ? "s" : ""
              } on the map.`
            : "Every sample is hidden, so the map is empty."}{" "}
          <button
            type="button"
            onClick={onShowAll}
            className="underline hover:text-stone-700"
          >
            Show all samples
          </button>
        </span>
      )}
    </div>
  );
}
