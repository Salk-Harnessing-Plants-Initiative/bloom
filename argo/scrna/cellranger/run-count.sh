#!/usr/bin/env bash
# Run `cellranger count` on one sample from S3 and upload the outputs; safe to re-run.
set -euo pipefail

: "${SAMPLE:?set SAMPLE (folder under raw_reads/)}"
: "${REFERENCE:?set REFERENCE (folder under reference_genome/)}"
BUCKET="${BUCKET:-bloomv2-workflows}"
RUN_ID="${RUN_ID:-$SAMPLE}"
FASTQ_SAMPLE="${FASTQ_SAMPLE:-$SAMPLE}"
CORES="${CORES:?set CORES to the CPU request of the job}"
MEM_GB="${MEM_GB:?set MEM_GB a little below the memory limit of the job}"
CREATE_BAM="${CREATE_BAM:-true}"
WORK_DIR="${WORK_DIR:-/work}"

OUT_URI="s3://${BUCKET}/runs_output/${RUN_ID}"

if aws s3 ls "${OUT_URI}/_SUCCESS" >/dev/null 2>&1; then
  echo "Already done: ${OUT_URI}/_SUCCESS exists, nothing to do."
  exit 0
fi

mkdir -p "${WORK_DIR}/fastq/${SAMPLE}" "${WORK_DIR}/ref/${REFERENCE}"
cd "${WORK_DIR}"

echo "Downloading reads and reference (skips files already present)..."
aws s3 sync "s3://${BUCKET}/raw_reads/${SAMPLE}/" "fastq/${SAMPLE}/"
aws s3 sync "s3://${BUCKET}/reference_genome/${REFERENCE}/" "ref/${REFERENCE}/"

# A killed pod leaves Martian's lock behind; only this job uses this folder.
rm -f "${RUN_ID}/_lock"

cellranger --version
cellranger count \
  --id="${RUN_ID}" \
  --transcriptome="ref/${REFERENCE}" \
  --fastqs="fastq/${SAMPLE}" \
  --sample="${FASTQ_SAMPLE}" \
  --create-bam="${CREATE_BAM}" \
  --localcores="${CORES}" \
  --localmem="${MEM_GB}"

echo "Comparing Cell Ranger's chemistry with the QC prediction..."
fastq-qc --fastq-dir "fastq/${SAMPLE}" --quick \
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
