// Importing a sample from SRA: the run IDs a scientist pastes and the name the sample gets.
// The same rules are enforced by request_scrna_cellranger_run and by fetch-sra.

// One lane per run, named L001-L009.
export const MAX_SRA_RUNS = 9;
// NCBI (SRR), ENA (ERR) and DDBJ (DRR) run IDs.
const SRA_RUN = /^[SED]RR[0-9]{6,10}$/;
// A sample is also Cell Ranger's run id: letters, digits, '_' or '-', at most 64.
const SAMPLE_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
// IDs people often have instead of run IDs, and what they are.
const NOT_A_RUN: [RegExp, string][] = [
  [/^GSE\d+$/i, "a GEO series"],
  [/^GSM\d+$/i, "a GEO sample"],
  [/^[SED]RP\d+$/i, "an SRA study"],
  [/^PRJ[A-Z]{2}\d+$/i, "a BioProject"],
  [/^[SED]RX\d+$/i, "an SRA experiment"],
  [/^[SED]RS\d+$/i, "an SRA sample"],
  [/^SAM[NED][A-Z]?\d+$/i, "a BioSample"],
];

export const SRA_RUN_SELECTOR_URL = "https://www.ncbi.nlm.nih.gov/Traces/study/";

// A run's page at NCBI, used as the dataset's source link.
export function sraRunUrl(run: string): string {
  return `https://www.ncbi.nlm.nih.gov/sra/${run}`;
}

export type ParsedSraRuns = { runs: string[]; problem: string | null };

// The run IDs in pasted text, split on commas and any whitespace, and what's wrong with them.
export function parseSraRuns(text: string): ParsedSraRuns {
  const runs = text.split(/[\s,]+/).filter(Boolean);
  if (runs.length === 0) return { runs, problem: "Paste at least one SRA run ID." };
  for (const run of runs) {
    const other = NOT_A_RUN.find(([pattern]) => pattern.test(run));
    if (other) {
      return {
        runs,
        problem: `${run} is ${other[1]}, not a run. Find its runs (SRR…) in SRA's Run Selector.`,
      };
    }
    if (!SRA_RUN.test(run)) {
      return { runs, problem: `${run} isn't an SRA run ID like SRR12046049.` };
    }
  }
  const repeated = runs.find((run, i) => runs.indexOf(run) !== i);
  if (repeated) return { runs, problem: `${repeated} is listed twice.` };
  if (runs.length > MAX_SRA_RUNS) {
    return {
      runs,
      problem: `One sample can have at most ${MAX_SRA_RUNS} runs; this lists ${runs.length}.`,
    };
  }
  return { runs, problem: null };
}

// What's wrong with a new sample's name, or null; registered names can't be reused.
export function newSampleNameProblem(name: string, registered: string[]): string | null {
  if (!name) return "Give the sample a name.";
  if (!SAMPLE_NAME.test(name) || name.includes("__")) {
    return "Sample names use letters, digits, '_' or '-' (no '__'), at most 64.";
  }
  if (registered.includes(name))
    return `${name} is already a registered sample; choose another name.`;
  return null;
}
