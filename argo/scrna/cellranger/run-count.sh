#!/usr/bin/env bash
# Run `cellranger count` on one sample staged in the run's shared folder (the stage steps copy the
# reads and reference there) and keep what the analysis steps need in RESULTS_DIR. Touches no S3.
# Safe to re-run.
set -euo pipefail

# Exit codes: 0 done or already done, 3 no reference, 4 no FASTQs, 5 cellranger count failed,
# 6 the sample name cannot be a Cell Ranger run id, 7 the FASTQs aren't named the Illumina way
# or a lane lacks R1 or R2.
readonly EXIT_NO_REFERENCE=3
readonly EXIT_NO_FASTQS=4
readonly EXIT_CELLRANGER_FAILED=5
readonly EXIT_BAD_SAMPLE_NAME=6
readonly EXIT_BAD_FASTQ_NAMES=7

: "${SAMPLE:?set SAMPLE (folder under raw_reads/)}"
: "${REFERENCE:?set REFERENCE (folder under reference_genome/)}"
RUN_ID="${RUN_ID:-$SAMPLE}"
# The FASTQs' name prefix for --sample; read from the file names unless set.
FASTQ_SAMPLE="${FASTQ_SAMPLE:-}"
CORES="${CORES:?set CORES to the CPU request of the job}"
MEM_GB="${MEM_GB:?set MEM_GB a little below the memory limit of the job}"
CREATE_BAM="${CREATE_BAM:-false}"
WORK_DIR="${WORK_DIR:-/work}"
REF_DIR="${REF_DIR:-${WORK_DIR}/ref/${REFERENCE}}"
FASTQ_DIR="${FASTQ_DIR:-${WORK_DIR}/fastq/${SAMPLE}}"

RESULTS_DIR="${RESULTS_DIR:?set RESULTS_DIR (the run folder on the share)}"
# Kept for the analysis steps: the matrix, Cell Ranger's metrics and the chemistry check.
KEEP=(filtered_feature_bc_matrix.h5 metrics_summary.csv qc/qc_summary.json)

# Cell Ranger's run id is the sample, so its report is titled with it; RUN_ID (which can be
# longer) names the run's folders and goes in the report's description.
CR_ID="${SAMPLE}"
if [[ ! "${CR_ID}" =~ ^[A-Za-z0-9_-]{1,64}$ ]]; then
  echo "ERROR: sample '${SAMPLE}' cannot be a Cell Ranger run id (letters, digits, '_' or '-', at most 64)"
  exit "${EXIT_BAD_SAMPLE_NAME}"
fi

if [ -f "${RESULTS_DIR}/outs/_SUCCESS" ]; then
  echo "Already done: ${RESULTS_DIR}/outs/_SUCCESS exists, nothing to do."
  exit 0
fi

mkdir -p "${WORK_DIR}" "${FASTQ_DIR}" "${REF_DIR}"
cd "${WORK_DIR}"

# Keep a log; on failure copy it with Cell Ranger's _errors to the run's folder, which is kept.
LOG="${WORK_DIR}/count.log"
exec > >(tee -a "${LOG}") 2>&1
keep_log_on_failure() {
  local status=$?
  if [ "${status}" -ne 0 ]; then
    find "${WORK_DIR}/${CR_ID}" -name _errors -exec cat {} + >> "${LOG}" 2>/dev/null || true
    mkdir -p "${RESULTS_DIR}/logs" && cp -- "${LOG}" "${RESULTS_DIR}/logs/count.log" || true
    echo "count failed (exit ${status}); log at ${RESULTS_DIR}/logs/count.log"
  fi
}
trap keep_log_on_failure EXIT

if [ ! -f "${REF_DIR}/reference.json" ]; then
  echo "ERROR: no staged Cell Ranger reference at ${REF_DIR} (expected reference.json)"
  exit "${EXIT_NO_REFERENCE}"
fi
if ! compgen -G "${FASTQ_DIR}/*.fastq*" >/dev/null; then
  echo "ERROR: no staged FASTQs at ${FASTQ_DIR}"
  exit "${EXIT_NO_FASTQS}"
fi
if [ -z "${FASTQ_SAMPLE}" ]; then
  FASTQ_SAMPLE="$(fastq-sample-prefix "${FASTQ_DIR}")" || exit "${EXIT_BAD_FASTQ_NAMES}"
fi
echo "FASTQ prefix: ${FASTQ_SAMPLE}"

# A killed pod leaves Martian's lock behind; only this job uses this folder.
rm -f "${CR_ID}/_lock"

cellranger --version
cellranger count \
  --id="${CR_ID}" \
  --description="${RUN_ID}" \
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
  --cellranger-chemistry "${CR_ID}/SC_RNA_COUNTER_CS/SC_MULTI_CORE/MULTI_CHEMISTRY_DETECTOR/DETECT_COUNT_CHEMISTRY/fork0/_outs" \
  --out-dir "${CR_ID}/outs/qc" \
  || echo "WARNING: chemistry check reported problems; see outs/qc/qc_summary.json"

echo "Keeping the matrix and metrics in ${RESULTS_DIR}/outs/..."
mkdir -p "${RESULTS_DIR}/outs"
for name in "${KEEP[@]}"; do
  if [ -f "${CR_ID}/outs/${name}" ]; then
    cp -- "${CR_ID}/outs/${name}" "${RESULTS_DIR}/outs/${name##*/}"
  fi
done
# Written last: its presence means the files above are in place.
printf 'run_id=%s\ncellranger=%s\nfinished=%s\n' \
  "${RUN_ID}" "$(cellranger --version | tail -1)" "$(date -u +%FT%TZ)" \
  > "${RESULTS_DIR}/outs/_SUCCESS"

echo "DONE -> ${RESULTS_DIR}/outs/"
