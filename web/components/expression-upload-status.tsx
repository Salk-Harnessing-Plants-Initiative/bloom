import type { Json } from "@/lib/database.types";

// What a dataset row says about how far its upload got.
export type UploadState = {
  source_checksum: string | null;
  ingested_at: string | null;
  metadata: Json | null;
};

// `bloomctl scrna hdf5 upload` records the file's fingerprint when it registers a dataset and
// the finish time only once every cell is stored, so a fingerprint with no finish time is a
// load that stopped or is still running. Datasets loaded before either existed have neither.
export function isIncompleteUpload(dataset: UploadState): boolean {
  return Boolean(dataset.source_checksum) && !dataset.ingested_at;
}

// How many cells the file holds, recorded at registration; null when it was not.
export function expectedCells(dataset: UploadState): number | null {
  const metadata = dataset.metadata;
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) return null;
  const value = (metadata as Record<string, Json | undefined>).expected_cells;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

const count = new Intl.NumberFormat("en-US");

export function IncompleteUploadBadge() {
  return (
    <span
      className="inline-flex items-center rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800"
      title="This dataset's upload did not finish, or is still loading"
    >
      Incomplete upload
    </span>
  );
}

export function IncompleteUploadNotice({
  loadedCells,
  expected,
}: {
  loadedCells: number | null;
  expected: number | null;
}) {
  const progress =
    loadedCells != null && expected != null
      ? `${count.format(loadedCells)} of ${count.format(expected)} cells are loaded`
      : loadedCells != null
        ? `${count.format(loadedCells)} cells are loaded so far`
        : "Not every cell is loaded";
  return (
    <div
      role="status"
      className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900"
    >
      <span className="font-semibold">Incomplete upload.</span> {progress}, so the map and
      counts are partial. The upload didn&apos;t finish, or is still loading; running the same{" "}
      <code className="font-mono text-xs">bloomctl scrna hdf5 upload</code> command again
      finishes it.
    </div>
  );
}
