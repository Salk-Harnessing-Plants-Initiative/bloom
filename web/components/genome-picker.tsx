"use client";

import type { GenomeChoice } from "@/lib/jbrowse/genome-choice";

interface Props {
  choices: { bloom: GenomeChoice[]; outside: GenomeChoice[] };
  /** The one open genome, or "" when none or several are open. */
  value: string;
  disabled?: boolean;
  onChoose: (name: string) => void;
}

/** The genome selector beside the gene search: Bloom's genomes, then the other HPI genomes. */
export function GenomePicker({ choices, value, disabled, onChoose }: Props) {
  const group = (label: string, list: GenomeChoice[]) =>
    list.length > 0 && (
      <optgroup label={label}>
        {list.map((g) => (
          <option key={g.name} value={g.name}>
            {g.label}
          </option>
        ))}
      </optgroup>
    );
  return (
    <select
      aria-label="Genome"
      className="w-72 rounded border border-stone-300 bg-white px-2 py-1 text-sm"
      value={value}
      disabled={disabled}
      onChange={(e) => e.target.value && onChoose(e.target.value)}
    >
      <option value="">Choose a genome…</option>
      {group("Bloom genomes", choices.bloom)}
      {group("Other HPI genomes", choices.outside)}
    </select>
  );
}
