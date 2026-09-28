#!/usr/bin/env bash
# Run `cellranger count` on one sample from S3 and upload the outputs; safe to re-run.
set -euo pipefail

# Exit codes: 0 done or already done, 3 no reference, 4 no FASTQs, 5 cellranger count failed.
readonly EXIT_NO_REFERENCE=3
readonly EXIT_NO_FASTQS=4
readonly EXIT_CELLRANGER_FAILED=5

: "${SAMPLE:?set SAMPLE (folder under raw_reads/)}"
: "${REFERENCE:?set REFERENCE (folder under reference_genome/)}"
BUCKET="${BUCKET:-bloomv2-workflows}"
RUN_ID="${RUN_ID:-$SAMPLE}"
FASTQ_SAMPLE="${FASTQ_SAMPLE:-$SAMPLE}"
CORES="${CORES:?set CORES to the CPU request of the job}"
MEM_GB="${MEM_GB:?set MEM_GB a little below the memory limit of the job}"
CREATE_BAM="${CREATE_BAM:-true}"
WORK_DIR="${WORK_DIR:-/work}"
REF_DIR="${REF_DIR:-${WORK_DIR}/ref/${REFERENCE}}"
FASTQ_DIR="${FASTQ_DIR:-${WORK_DIR}/fastq/${SAMPLE}}"

OUT_URI="s3://${BUCKET}/runs_output/${RUN_ID}"

if aws s3 ls "${OUT_URI}/_SUCCESS" >/dev/null 2>&1; then
  echo "Already done: ${OUT_URI}/_SUCCESS exists, nothing to do."
  exit 0
fi

mkdir -p "${WORK_DIR}" "${FASTQ_DIR}" "${REF_DIR}"
cd "${WORK_DIR}"

# Keep a log; on failure upload it with Cell Ranger's _errors so the reason is in S3.
LOG="${WORK_DIR}/count.log"
exec > >(tee -a "${LOG}") 2>&1
upload_log_on_failure() {
  local status=$?
  if [ "${status}" -ne 0 ]; then
    find "${WORK_DIR}/${RUN_ID}" -name _errors -exec cat {} + >> "${LOG}" 2>/dev/null || true
    aws s3 cp "${LOG}" "${OUT_URI}/logs/count.log" || true
    echo "count failed (exit ${status}); log at ${OUT_URI}/logs/count.log"
  fi
}
trap upload_log_on_failure EXIT

echo "Downloading reads and reference (skips files already present)..."
aws s3 sync "s3://${BUCKET}/raw_reads/${SAMPLE}/" "${FASTQ_DIR}/"
aws s3 sync "s3://${BUCKET}/reference_genome/${REFERENCE}/" "${REF_DIR}/"

# sync from a missing prefix copies nothing and still exits 0
if [ ! -f "${REF_DIR}/reference.json" ]; then
  echo "ERROR: no Cell Ranger reference at s3://${BUCKET}/reference_genome/${REFERENCE}/ (expected reference.json)"
  exit "${EXIT_NO_REFERENCE}"
fi
if ! ls "${FASTQ_DIR}"/*.fastq.gz >/dev/null 2>&1; then
  echo "ERROR: no FASTQs at s3://${BUCKET}/raw_reads/${SAMPLE}/"
  exit "${EXIT_NO_FASTQS}"
fi

# A killed pod leaves Martian's lock behind; only this job uses this folder.
rm -f "${RUN_ID}/_lock"

cellranger --version
cellranger count \
  --id="${RUN_ID}" \
  --transcriptome="${REF_DIR}" \
  --fastqs="${FASTQ_DIR}" \
  --sample="${FASTQ_SAMPLE}" \
  --create-bam="${CREATE_BAM}" \
  --localcores="${CORES}" \
  --localmem="${MEM_GB}" \
  || { echo "ERROR: cellranger count failed; Cell Ranger's errors are in the log"; exit "${EXIT_CELLRANGER_FAILED}"; }

echo "Comparing Cell Ranger's chemistry with the QC prediction..."
fastq-qc --fastq-dir "${FASTQ_DIR}" --quick \
  --whitelist-dir "${CELLRANGER_HOME:?set by the image}/lib/python/cellranger/barcodes" \
  --cellranger-chemistry "${RUN_ID}/SC_RNA_COUNTER_CS/SC_MULTI_CORE/MULTI_CHEMISTRY_DETECTOR/DETECT_COUNT_CHEMISTRY/fork0/_outs" \
  --out-dir "${RUN_ID}/outs/qc" \
  || echo "WARNING: chemistry check reported problems; see outs/qc/qc_summary.json"

echo "Uploading outputs..."
aws s3 sync "${RUN_ID}/outs/" "${OUT_URI}/outs/"
printf 'run_id=%s\ncellranger=%s\nfinished=%s\n' \
  "${RUN_ID}" "$(cellranger --version | tail -1)" "$(date -u +%FT%TZ)" \
  | aws s3 cp - "${OUT_URI}/_SUCCESS"

echo "DONE -> ${OUT_URI}"
