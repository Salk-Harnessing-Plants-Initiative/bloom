#!/usr/bin/env bash
# FASTQ QC for one sample's reads, already in WORK_DIR/fastq/<sample>/ (put there by fetch-sra or
# stage): counts, lengths, chemistry guess, FastQC and a README, in WORK_DIR/qc/<run-id>/.
# The report stays there; nothing is read from or written to S3.
set -euo pipefail

: "${SAMPLE:?set SAMPLE}"
RUN_ID="${RUN_ID:-$SAMPLE}"
SAMPLE_READS="${SAMPLE_READS:-200000}"
CORES="${CORES:?set CORES to the CPU request of the job}"
WORK_DIR="${WORK_DIR:-/work}"

FASTQ_DIR="${WORK_DIR}/fastq/${SAMPLE}"
OUT_DIR="${WORK_DIR}/qc/${RUN_ID}"
mkdir -p "${OUT_DIR}/fastqc"
exec > >(tee -a "${OUT_DIR}/run.log") 2>&1

status=0
fastq-qc --fastq-dir "${FASTQ_DIR}" --out-dir "${OUT_DIR}" --sample-reads "${SAMPLE_READS}" \
  --whitelist-dir "${CELLRANGER_HOME:?set by the image}/lib/python/cellranger/barcodes" || status=$?

# R1 always fails FastQC's base-content checks on 10x data (it is barcode + UMI); judge quality on R2.
fastqc --threads "${CORES}" --outdir "${OUT_DIR}/fastqc" "${FASTQ_DIR}"/*_R[12]_001.fastq.gz || status=$?
qc-report "${OUT_DIR}" || status=$?

echo "QC finished with exit ${status}; report in ${OUT_DIR}/"
exit "${status}"
