"use client";

interface Props {
  on: boolean;
  onChange: (on: boolean) => void;
}

/** Shows or hides every transgene count on a map at once. */
export function TransgeneToggle({ on, onChange }: Props) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={() => onChange(!on)}
      title={on ? "Hide the transgene counts" : "Show the transgene counts"}
      className={`inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-emerald-400 ${
        on
          ? "border-emerald-600 bg-emerald-600 text-white"
          : "border-stone-300 bg-white text-stone-600 hover:border-stone-400"
      }`}
    >
      <span
        aria-hidden
        className={`relative inline-block h-3.5 w-6 rounded-full transition-colors ${
          on ? "bg-emerald-300" : "bg-stone-300"
        }`}
      >
        <span
          className={`absolute top-0.5 h-2.5 w-2.5 rounded-full bg-white shadow transition-all ${
            on ? "left-3" : "left-0.5"
          }`}
        />
      </span>
      Transgene counts
    </button>
  );
}

export default TransgeneToggle;
