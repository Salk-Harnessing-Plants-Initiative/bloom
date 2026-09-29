"use client";

import { useState } from "react";
import {
  buildRunMetadata,
  datasetDetailsProblem,
  formatBytes,
  isStartedRun,
  startRunErrorMessage,
  type AttributeRow,
  type DataOrigin,
  type RnaseqReference,
  type RnaseqSample,
  type StartedRun,
} from "@/lib/scrna-jobs";
import type { SpeciesOption } from "@/lib/species-options";
import ScrnaDataOrigin from "./scrna-data-origin";
import ScrnaSpeciesPicker from "./scrna-species-picker";

type Status = "idle" | "submitting" | "done" | "error";

const fieldClass =
  "mt-1 block w-full rounded-md border border-stone-300 bg-white px-3 py-2 text-sm text-stone-800 focus:border-lime-700 focus:outline-none focus:ring-1 focus:ring-lime-700 disabled:bg-stone-100";
const labelClass = "block text-sm font-medium text-stone-700";
const groupHeadingClass =
  "mb-3 text-xs uppercase tracking-widest text-stone-500";

function sampleLabel(sample: RnaseqSample): string {
  const details = [
    sample.fastq_count != null
      ? `${sample.fastq_count} FASTQ${sample.fastq_count === 1 ? "" : "s"}`
      : null,
    formatBytes(sample.total_bytes),
  ].filter(Boolean);
  return details.length ? `${sample.name} (${details.join(", ")})` : sample.name;
}

function referenceLabel(reference: RnaseqReference): string {
  return reference.description
    ? `${reference.name} — ${reference.description}`
    : reference.name;
}

export default function ScrnaJobSubmit({
  samples,
  references,
  species,
}: {
  samples: RnaseqSample[];
  references: RnaseqReference[];
  species: SpeciesOption[];
}) {
  const [open, setOpen] = useState(false);
  const [sample, setSample] = useState("");
  const [reference, setReference] = useState("");
  const [speciesId, setSpeciesId] = useState<number | null>(null);
  const [datasetName, setDatasetName] = useState("");
  const [accession, setAccession] = useState("");
  const [experimentName, setExperimentName] = useState("");
  const [origin, setOrigin] = useState<DataOrigin>("hpi");
  const [sourceUrl, setSourceUrl] = useState("");
  const [citation, setCitation] = useState("");
  const [attributes, setAttributes] = useState<AttributeRow[]>([
    { key: "", value: "" },
  ]);
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState("");
  const [started, setStarted] = useState<StartedRun | null>(null);

  const submitting = status === "submitting";
  const details = {
    speciesId,
    datasetName,
    accession,
    experimentName,
    origin,
    sourceUrl,
    citation,
    attributes,
  };
  const detailsProblem = datasetDetailsProblem(details);
  const canSubmit = Boolean(sample && reference) && !detailsProblem && !submitting;

  function updateAttribute(index: number, change: Partial<AttributeRow>) {
    setAttributes((rows) =>
      rows.map((row, i) => (i === index ? { ...row, ...change } : row))
    );
  }

  function removeAttribute(index: number) {
    setAttributes((rows) =>
      rows.length === 1 ? [{ key: "", value: "" }] : rows.filter((_, i) => i !== index)
    );
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setStatus("submitting");
    setMessage("");
    setStarted(null);

    let response: Response;
    try {
      response = await fetch("/api/scrna/cellranger/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sample,
          reference,
          metadata: buildRunMetadata(details),
        }),
      });
    } catch {
      setMessage("Could not reach the job service.");
      setStatus("error");
      return;
    }

    const body = await response.json().catch(() => null);
    if (!response.ok) {
      setMessage(startRunErrorMessage(response.status, body?.detail));
      setStatus("error");
      return;
    }
    if (!isStartedRun(body)) {
      setMessage("The job service returned an unexpected response.");
      setStatus("error");
      return;
    }
    setStarted(body);
    setStatus("done");
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-md bg-lime-700 px-4 py-2.5 text-sm font-medium text-stone-50 hover:bg-lime-800"
      >
        Submit scRNA job
      </button>
    );
  }

  return (
    <section
      aria-labelledby="scrna-job-heading"
      className="max-w-2xl rounded-md border border-stone-200 bg-stone-50 p-6"
    >
      <div className="mb-4 flex items-baseline justify-between gap-4">
        <h2 id="scrna-job-heading" className="text-xl font-serif italic">
          Submit scRNA job
        </h2>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-sm text-stone-500 hover:text-stone-800"
        >
          Close
        </button>
      </div>
      <p className="mb-5 text-sm text-stone-500">
        Runs the Cell Ranger protocol (cellranger count) on one sample&apos;s
        FASTQs: it aligns the reads to the chosen reference genome, calls cells
        from their barcodes, and counts UMIs per gene in each cell. The output
        is a filtered feature-barcode matrix (matrix, features and barcodes),
        ready to load into Seurat or Scanpy. The results are saved to Bloom
        when the run finishes.
      </p>

      <form onSubmit={submit} className="space-y-6">
        <fieldset className="space-y-4" disabled={submitting}>
          <legend className={groupHeadingClass}>Run</legend>

          <label className={labelClass}>
            Job type
            <select className={fieldClass} value="cellranger-count" disabled>
              <option value="cellranger-count">Cell Ranger count</option>
            </select>
          </label>

          <label className={labelClass}>
            Sample
            <select
              className={fieldClass}
              value={sample}
              onChange={(e) => setSample(e.target.value)}
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

          <label className={labelClass}>
            Reference genome
            <select
              className={fieldClass}
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              disabled={references.length === 0}
            >
              <option value="">
                {references.length
                  ? "Choose a reference"
                  : "No references registered yet"}
              </option>
              {references.map((r) => (
                <option key={r.name} value={r.name}>
                  {referenceLabel(r)}
                </option>
              ))}
            </select>
          </label>

          {samples.length === 0 || references.length === 0 ? (
            <p className="text-sm text-stone-500">
              Samples and references are added by a Bloom admin once their
              files are in storage.
            </p>
          ) : null}
        </fieldset>

        <fieldset className="space-y-4" disabled={submitting}>
          <legend className={groupHeadingClass}>Dataset details</legend>
          <p className="-mt-1 text-sm text-stone-500">
            Used to add the results to the Expression pages when the run
            finishes.
          </p>

          <ScrnaSpeciesPicker
            species={species}
            value={speciesId}
            onChange={setSpeciesId}
            fieldClass={fieldClass}
            labelClass={labelClass}
          />

          <label className={labelClass}>
            Dataset name
            <input
              className={fieldClass}
              value={datasetName}
              onChange={(e) => setDatasetName(e.target.value)}
              placeholder="e.g. Col-0 root tip, 7 DAG"
            />
          </label>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className={labelClass}>
              Accession or genotype{" "}
              <span className="font-normal text-stone-400">(optional)</span>
              <input
                className={fieldClass}
                value={accession}
                onChange={(e) => setAccession(e.target.value)}
                placeholder="e.g. Col-0"
              />
            </label>
            <label className={labelClass}>
              Experiment{" "}
              <span className="font-normal text-stone-400">(optional)</span>
              <input
                className={fieldClass}
                value={experimentName}
                onChange={(e) => setExperimentName(e.target.value)}
                placeholder="e.g. Root salt stress 2026"
              />
            </label>
          </div>

          <ScrnaDataOrigin
            origin={origin}
            sourceUrl={sourceUrl}
            citation={citation}
            onOrigin={setOrigin}
            onSourceUrl={setSourceUrl}
            onCitation={setCitation}
            fieldClass={fieldClass}
            labelClass={labelClass}
          />

          <div>
            <div className={labelClass}>
              Other details{" "}
              <span className="font-normal text-stone-400">(optional)</span>
            </div>
            <p className="mt-0.5 text-sm text-stone-500">
              Anything else about the sample, such as tissue, days after
              germination, treatment or replicate.
            </p>
            <ul className="mt-2 space-y-2">
              {attributes.map((row, index) => (
                <li key={index} className="flex items-center gap-2">
                  <input
                    aria-label={`Field ${index + 1} name`}
                    className={`${fieldClass} mt-0`}
                    value={row.key}
                    onChange={(e) => updateAttribute(index, { key: e.target.value })}
                    placeholder="Name, e.g. tissue"
                  />
                  <input
                    aria-label={`Field ${index + 1} value`}
                    className={`${fieldClass} mt-0`}
                    value={row.value}
                    onChange={(e) => updateAttribute(index, { value: e.target.value })}
                    placeholder="Value, e.g. root"
                  />
                  <button
                    type="button"
                    aria-label={`Remove field ${index + 1}`}
                    onClick={() => removeAttribute(index)}
                    className="shrink-0 px-2 text-lg text-stone-400 hover:text-stone-700"
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
            <button
              type="button"
              onClick={() => setAttributes((rows) => [...rows, { key: "", value: "" }])}
              className="mt-2 text-sm text-lime-700 hover:underline underline-offset-4"
            >
              + Add field
            </button>
          </div>
        </fieldset>

        <div className="flex flex-wrap items-center gap-4">
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded-md bg-lime-700 px-4 py-2.5 text-sm font-medium text-stone-50 hover:bg-lime-800 disabled:opacity-50"
          >
            {submitting ? "Starting…" : "Start run"}
          </button>
          {status === "error" ? (
            <p role="alert" className="text-sm text-red-700">
              {message}
            </p>
          ) : sample && reference && detailsProblem ? (
            <p className="text-sm text-stone-500">{detailsProblem}</p>
          ) : null}
        </div>
      </form>

      {started ? (
        <p role="status" className="mt-5 text-sm text-stone-700">
          Run <span className="font-medium tabular-nums">{started.run_id}</span>{" "}
          queued: <span className="font-medium">{started.sample}</span> against{" "}
          <span className="font-medium">{started.reference}</span>.
        </p>
      ) : null}
    </section>
  );
}
