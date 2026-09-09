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
  onToggle: (name: string) => void;
  onShowAll: () => void;
}

export function ExpressionSampleToggles({
  samples,
  hidden,
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
                ? "border-stone-200 bg-stone-50 text-stone-400"
                : "border-lime-300 bg-lime-50 text-stone-700 hover:border-lime-400"
            }`}
          >
            <span className="font-medium">{sample.name}</span>
            <span className="tabular-nums text-[10px] text-stone-500">
              {fmt.format(sample.count)}
            </span>
          </button>
        );
      })}
      {allHidden && (
        <span className="text-xs text-stone-500">
          Every sample is hidden, so the map is empty.{" "}
          <button
            type="button"
            onClick={onShowAll}
            className="underline hover:text-stone-700"
          >
            Show all
          </button>
        </span>
      )}
    </div>
  );
}
