"use client";

import { useState } from "react";
import {
  buildRunMetadata,
  datasetDetailsProblem,
  isStartedRun,
  startRunErrorMessage,
  type AttributeRow,
  type DataOrigin,
  type RnaseqReference,
  type RnaseqSample,
  type StartedRun,
} from "@/lib/scrna-jobs";
import type { SpeciesOption } from "@/lib/species-options";
import { newSampleNameProblem, parseSraRuns, sraRunUrl } from "@/lib/sra-runs";
import ScrnaDataOrigin from "./scrna-data-origin";
import ScrnaJobQueued from "./scrna-job-queued";
import ScrnaOtherDetails from "./scrna-other-details";
import ScrnaSampleSource, { type SampleSource } from "./scrna-sample-source";
import ScrnaSpeciesPicker from "./scrna-species-picker";

type Status = "idle" | "submitting" | "done" | "error";

const fieldClass =
  "mt-1 block w-full rounded-md border border-stone-300 bg-white px-3 py-2 text-sm text-stone-800 focus:border-lime-700 focus:outline-none focus:ring-1 focus:ring-lime-700 disabled:bg-stone-100";
const labelClass = "block text-sm font-medium text-stone-700";
const groupHeadingClass =
  "mb-3 text-xs uppercase tracking-widest text-stone-500";

function referenceLabel(reference: RnaseqReference): string {
  return reference.description
    ? `${reference.name} — ${reference.description}`
    : reference.name;
}

export default function ScrnaJobSubmit({
  samples,
  references,
  species,
  startedBy,
}: {
  samples: RnaseqSample[];
  references: RnaseqReference[];
  species: SpeciesOption[];
  // The signed-in user's email, shown on the confirmation.
  startedBy: string | null;
}) {
  const [open, setOpen] = useState(false);
  // Species added from the form stay listed after it is closed and reopened.
  const [speciesOptions, setSpeciesOptions] = useState(species);
  const [source, setSource] = useState<SampleSource>("registered");
  const [sample, setSample] = useState("");
  const [sraText, setSraText] = useState("");
  // The new sample's name follows the first run ID until it's edited.
  const [newName, setNewName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [reference, setReference] = useState("");
  const [speciesId, setSpeciesId] = useState<number | null>(null);
  const [datasetName, setDatasetName] = useState("");
  const [accession, setAccession] = useState("");
  const [experimentName, setExperimentName] = useState("");
  const [origin, setOrigin] = useState<DataOrigin>("hpi");
  const [sourceUrl, setSourceUrl] = useState("");
  // For an SRA import the source link follows the first run until it's edited.
  const [sourceEdited, setSourceEdited] = useState(false);
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
  const sra = parseSraRuns(sraText);
  const sampleReady =
    source === "registered"
      ? Boolean(sample)
      : !sra.problem &&
        !newSampleNameProblem(
          newName.trim(),
          samples.map((s) => s.name)
        );
  const canSubmit = sampleReady && Boolean(reference) && !detailsProblem && !submitting;

  function changeSource(next: SampleSource) {
    setSource(next);
    // Data imported from SRA is public.
    if (next === "sra") setOrigin("public");
  }

  function changeSraText(text: string) {
    setSraText(text);
    const first = parseSraRuns(text).runs[0] ?? "";
    if (!nameEdited) setNewName(first);
    if (!sourceEdited) setSourceUrl(first ? sraRunUrl(first) : "");
  }

  function changeNewName(name: string) {
    setNewName(name);
    setNameEdited(true);
  }

  function changeSourceUrl(url: string) {
    setSourceUrl(url);
    setSourceEdited(true);
  }

  function addSpeciesOption(option: SpeciesOption) {
    setSpeciesOptions((current) =>
      current.some((o) => o.id === option.id)
        ? current
        : [...current, option].sort((a, b) => a.label.localeCompare(b.label))
    );
  }

  // Ready for the next sample: the per-sample fields are cleared, the rest kept.
  function startAnother() {
    setSample("");
    setSraText("");
    setNewName("");
    setNameEdited(false);
    setDatasetName("");
    setStarted(null);
    setMessage("");
    setStatus("idle");
  }

  function close() {
    if (started) startAnother();
    setOpen(false);
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
        body: JSON.stringify(
          source === "registered"
            ? { sample, reference, metadata: buildRunMetadata(details) }
            : {
                sample: newName.trim(),
                reference,
                metadata: buildRunMetadata(details),
                sra_runs: sra.runs,
              }
        ),
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
          onClick={close}
          className="text-sm text-stone-500 hover:text-stone-800"
        >
          Close
        </button>
      </div>
      {started ? (
        <ScrnaJobQueued
          run={started}
          datasetName={datasetName.trim()}
          speciesLabel={speciesOptions.find((o) => o.id === speciesId)?.label ?? null}
          startedBy={startedBy}
          onStartAnother={startAnother}
        />
      ) : (
        <>
          <p className="mb-5 text-sm text-stone-500">
            Runs the Cell Ranger protocol (cellranger count) on one sample&apos;s
            FASTQs: it aligns the reads to the chosen reference genome, calls cells
            from their barcodes, and counts UMIs per gene in each cell, then
            clusters the cells and computes a UMAP. The result is one AnnData file
            (.h5ad) with the counts, clusters and UMAP. A sample can also be
            imported from SRA: the run downloads it first and registers it for
            later runs.
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

              <ScrnaSampleSource
                source={source}
                onSource={changeSource}
                samples={samples}
                sample={sample}
                onSample={setSample}
                sraText={sraText}
                onSraText={changeSraText}
                newName={newName}
                onNewName={changeNewName}
                fieldClass={fieldClass}
                labelClass={labelClass}
              />

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

              {(source === "registered" && samples.length === 0) ||
              references.length === 0 ? (
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
                species={speciesOptions}
                value={speciesId}
                onChange={setSpeciesId}
                onAdded={addSpeciesOption}
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
                onSourceUrl={changeSourceUrl}
                onCitation={setCitation}
                fieldClass={fieldClass}
                labelClass={labelClass}
              />

              <ScrnaOtherDetails
                rows={attributes}
                onChange={setAttributes}
                fieldClass={fieldClass}
                labelClass={labelClass}
              />
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
              ) : sampleReady && reference && detailsProblem ? (
                <p className="text-sm text-stone-500">{detailsProblem}</p>
              ) : null}
            </div>
          </form>
        </>
      )}
    </section>
  );
}
