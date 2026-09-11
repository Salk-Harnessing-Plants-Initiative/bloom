"use client";

interface Props {
  /** The row the map is coloured by: "Datasets", "Cell type", or a label's name. */
  title: string;
  levels: string[];
  /** A CSS colour per value, matching the map. */
  colours: string[];
  /** Per value, the points the other filters leave on the map. */
  counts: number[];
  /** Points with no value in this row; drawn in grey, never hidden by it. */
  noValueCount: number;
  hidden: ReadonlySet<number>;
  focused: ReadonlySet<number>;
  onToggle: (level: number) => void;
  onFocus: (level: number) => void;
  onShowAll: () => void;
  onHideAll: () => void;
}

/** The values of the row the map is coloured by, as tags that wrap across the
 *  width: each with its colour and count, a click to hide it and a button to
 *  focus on it. */
export function IntegrationLegend({
  title,
  levels,
  colours,
  counts,
  noValueCount,
  hidden,
  focused,
  onToggle,
  onFocus,
  onShowAll,
  onHideAll,
}: Props) {
  const fmt = new Intl.NumberFormat("en-US");

  return (
    <div data-testid="integration-legend" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-[10px] uppercase tracking-widest text-stone-500">
          {title} ({levels.length})
        </span>
        <button type="button" onClick={onShowAll} className="text-[11px] text-lime-700 hover:underline">
          Show all
        </button>
        <button type="button" onClick={onHideAll} className="text-[11px] text-stone-500 hover:underline">
          Hide all
        </button>
        {noValueCount > 0 && (
          <span className="text-[11px] text-stone-500">
            {fmt.format(noValueCount)} {noValueCount === 1 ? "point has" : "points have"} no{" "}
            {title}; {noValueCount === 1 ? "it is" : "they are"} drawn in faint grey.
          </span>
        )}
      </div>

      <ul className="flex max-h-44 flex-wrap gap-1.5 overflow-y-auto">
        {levels.map((name, i) => {
          const isHidden = hidden.has(i);
          const isFocused = focused.has(i);
          return (
            <li key={name} className="inline-flex items-center gap-0.5">
              <button
                type="button"
                onClick={() => onToggle(i)}
                aria-pressed={!isHidden}
                aria-label={`${isHidden ? "Show" : "Hide"} ${name}`}
                title={isHidden ? `Show ${name}` : `Hide ${name}`}
                className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs transition-colors ${
                  isHidden
                    ? "border-stone-200 bg-stone-50 text-stone-400"
                    : "border-stone-200 bg-white text-stone-700 hover:border-stone-400"
                }`}
              >
                <span
                  aria-hidden
                  className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                  style={
                    isHidden
                      ? { border: `1px solid ${colours[i]}` }
                      : { background: colours[i] }
                  }
                />
                <span
                  className={`max-w-[24ch] truncate ${isHidden ? "line-through decoration-stone-300" : ""}`}
                  title={name}
                >
                  {name}
                </span>
                <span className="text-[10px] tabular-nums text-stone-500">
                  {fmt.format(counts[i] ?? 0)}
                </span>
              </button>
              <button
                type="button"
                onClick={() => onFocus(i)}
                aria-pressed={isFocused}
                aria-label={`Focus on ${name}`}
                title={
                  isFocused
                    ? `Stop focusing on ${name}`
                    : `Focus on ${name}: grey out every other point`
                }
                className={`rounded-md border px-1.5 py-0.5 text-xs leading-none transition-colors ${
                  isFocused
                    ? "border-stone-700 bg-stone-700 text-white"
                    : "border-stone-200 bg-white text-stone-400 hover:border-stone-400 hover:text-stone-700"
                }`}
              >
                ◎
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default IntegrationLegend;
