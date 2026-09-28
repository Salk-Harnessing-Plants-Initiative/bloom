#!/usr/bin/env bash
# FASTQ QC for one sample in S3: counts, lengths, chemistry guess, FastQC and a README, written to runs_output/<run-id>/qc/.
set -euo pipefail

: "${SAMPLE:?set SAMPLE (folder under raw_reads/)}"
BUCKET="${BUCKET:-bloomv2-workflows}"
RUN_ID="${RUN_ID:-$SAMPLE}"
SAMPLE_READS="${SAMPLE_READS:-200000}"
CORES="${CORES:?set CORES to the CPU request of the job}"
WORK_DIR="${WORK_DIR:-/work}"

FASTQ_DIR="${WORK_DIR}/fastq/${SAMPLE}"
OUT_DIR="${WORK_DIR}/qc/${RUN_ID}"
QC_URI="s3://${BUCKET}/runs_output/${RUN_ID}/qc"
mkdir -p "${FASTQ_DIR}" "${OUT_DIR}/fastqc"
exec > >(tee -a "${OUT_DIR}/run.log") 2>&1

echo "Downloading reads (skips files already present)..."
aws s3 sync "s3://${BUCKET}/raw_reads/${SAMPLE}/" "${FASTQ_DIR}/"

status=0
fastq-qc --fastq-dir "${FASTQ_DIR}" --out-dir "${OUT_DIR}" --sample-reads "${SAMPLE_READS}" \
  --whitelist-dir "${CELLRANGER_HOME:?set by the image}/lib/python/cellranger/barcodes" || status=$?

# R1 always fails FastQC's base-content checks on 10x data (it is barcode + UMI); judge quality on R2.
fastqc --threads "${CORES}" --outdir "${OUT_DIR}/fastqc" "${FASTQ_DIR}"/*_R[12]_001.fastq.gz || status=$?
qc-report "${OUT_DIR}" || status=$?

# Upload the report even when QC fails, so the reason is in S3.
echo "QC finished with exit ${status}; uploading report to ${QC_URI}/"
aws s3 sync "${OUT_DIR}/" "${QC_URI}/"
exit "${status}"
