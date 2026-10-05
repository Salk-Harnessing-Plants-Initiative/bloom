// What a dataset row says about whether its upload finished.
export type UploadState = {
  source_checksum: string | null;
  ingested_at: string | null;
};

// bloomctl records the file's fingerprint first and the finish time last; older loaders write
// both at once or neither, so only an upload that stopped or is still running matches.
export function isIncompleteUpload(dataset: UploadState): boolean {
  return Boolean(dataset.source_checksum) && !dataset.ingested_at;
}

export function IncompleteUploadBadge() {
  return (
    <span
      className="inline-flex items-center rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800"
      title="This dataset is incomplete or is still being uploaded"
    >
      Incomplete upload
    </span>
  );
}

export function IncompleteUploadNotice() {
  return (
    <div
      role="note"
      className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900"
    >
      <span className="font-semibold">Incomplete upload.</span> This dataset is incomplete or is
      still being uploaded, so what&apos;s shown here may be partial.
    </div>
  );
}
