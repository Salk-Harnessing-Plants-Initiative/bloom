"use client";

import { formatBytes, type RnaseqSample } from "@/lib/scrna-jobs";
import {
  MAX_SRA_RUNS,
  SRA_RUN_SELECTOR_URL,
  newSampleNameProblem,
  parseSraRuns,
} from "@/lib/sra-runs";

export type SampleSource = "registered" | "sra";

function sampleLabel(sample: RnaseqSample): string {
  const details = [
    sample.fastq_count != null
      ? `${sample.fastq_count} FASTQ${sample.fastq_count === 1 ? "" : "s"}`
      : null,
    formatBytes(sample.total_bytes),
  ].filter(Boolean);
  return details.length ? `${sample.name} (${details.join(", ")})` : sample.name;
}

// The sample a run counts: one already registered, or one imported from SRA under a new name.
export default function ScrnaSampleSource({
  source,
  onSource,
  samples,
  sample,
  onSample,
  sraText,
  onSraText,
  newName,
  onNewName,
  fieldClass,
  labelClass,
}: {
  source: SampleSource;
  onSource: (source: SampleSource) => void;
  samples: RnaseqSample[];
  sample: string;
  onSample: (name: string) => void;
  sraText: string;
  onSraText: (text: string) => void;
  newName: string;
  onNewName: (name: string) => void;
  fieldClass: string;
  labelClass: string;
}) {
  const parsed = parseSraRuns(sraText);
  const nameProblem = newSampleNameProblem(
    newName.trim(),
    samples.map((s) => s.name),
  );

  return (
    <div className="space-y-4">
      <fieldset>
        <legend className={labelClass}>Sample</legend>
        <div className="mt-1 flex flex-wrap gap-x-6 gap-y-1 text-sm text-stone-700">
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="sample-source"
              value="registered"
              checked={source === "registered"}
              onChange={() => onSource("registered")}
            />
            A registered sample
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="sample-source"
              value="sra"
              checked={source === "sra"}
              onChange={() => onSource("sra")}
            />
            Import from SRA
          </label>
        </div>
      </fieldset>

      {source === "registered" ? (
        <label className={labelClass}>
          Registered sample
          <select
            className={fieldClass}
            value={sample}
            onChange={(e) => onSample(e.target.value)}
            disabled={samples.length === 0}
          >
            <option value="">
              {samples.length ? "Choose a sample" : "No samples registered yet"}
            </option>
            {samples.map((s) => (
              <option key={s.name} value={s.name}>
                {sampleLabel(s)}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <div className="space-y-4 rounded-md border border-stone-200 bg-white p-4">
          <label className={labelClass}>
            SRA run IDs
            <textarea
              className={`${fieldClass} font-mono`}
              rows={3}
              value={sraText}
              onChange={(e) => onSraText(e.target.value)}
              placeholder={"SRR28503597\nSRR28503598"}
              aria-describedby="sra-runs-help"
            />
          </label>
          <p id="sra-runs-help" className="-mt-2 text-sm text-stone-500">
            One per line or separated by commas: {MAX_SRA_RUNS} at most, all from the same
            10x library, since each becomes one lane. Run IDs start with SRR, ERR or DRR.
            For a GEO sample (GSM…), look up its runs in{" "}
            <a
              href={SRA_RUN_SELECTOR_URL}
              target="_blank"
              rel="noreferrer"
              className="text-lime-800 underline"
            >
              SRA&apos;s Run Selector
            </a>
            .
          </p>

          {sraText.trim() && parsed.problem ? (
            <p role="alert" className="text-sm text-red-700">
              {parsed.problem}
            </p>
          ) : !parsed.problem ? (
            <ol
              className="flex flex-wrap gap-2 text-xs text-stone-600"
              aria-label="Lanes"
            >
              {parsed.runs.map((run, i) => (
                <li key={run} className="rounded bg-stone-100 px-2 py-1 font-mono">
                  L{String(i + 1).padStart(3, "0")} {run}
                </li>
              ))}
            </ol>
          ) : null}

          <label className={labelClass}>
            Sample name
            <input
              className={fieldClass}
              value={newName}
              onChange={(e) => onNewName(e.target.value)}
              placeholder="e.g. col0_root_tip_rep1"
            />
          </label>
          {newName.trim() && nameProblem ? (
            <p role="alert" className="-mt-2 text-sm text-red-700">
              {nameProblem}
            </p>
          ) : (
            <p className="-mt-2 text-sm text-stone-500">
              The name the sample is registered under once it&apos;s downloaded, so later
              runs can use it.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
