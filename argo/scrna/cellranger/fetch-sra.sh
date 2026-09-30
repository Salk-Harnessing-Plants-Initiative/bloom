#!/usr/bin/env bash
# Download one sample's SRA runs into s3://<bucket>/raw_reads/<sample>/ as Illumina-named FASTQs.
#
# Each run becomes one lane, in the order given. Its reads are downloaded with their technical
# reads, which is where 10x's barcode read is kept, and told apart by length: 26-28 bp is R1
# (barcode and UMI), 6-12 bp an index read (I1, then I2), 50 bp or more R2 (cDNA). A folder
# that already holds these runs (its .sra-runs marker matches) is left as it is.
#
# Env: SAMPLE, SRA_RUNS (accessions, comma- or space-separated), BUCKET, WORK_DIR, THREADS,
#      OUTPUT_DIR (where fastq_count and total_bytes are written for Argo).
# Exit codes: 0 done or already there, 7 misnamed files (fastq-sample-prefix), 10 a run
#      couldn't be downloaded, 11 a run lacks the barcode or cDNA read, 12 the folder holds
#      other FASTQs, 6 a bad sample name or accession list.
set -euo pipefail

readonly EXIT_BAD_INPUT=6
readonly EXIT_BAD_FASTQ_NAMES=7
readonly EXIT_DOWNLOAD_FAILED=10
readonly EXIT_READS_UNUSABLE=11
readonly EXIT_FOLDER_TAKEN=12
readonly MAX_RUNS=9
# Reads sampled from each file to find its read length.
readonly SAMPLED_READS=4000
readonly MARKER=".sra-runs"

: "${SAMPLE:?set SAMPLE (the folder under raw_reads/)}"
: "${SRA_RUNS:?set SRA_RUNS (run accessions)}"
BUCKET="${BUCKET:-bloomv2-workflows}"
WORK_DIR="${WORK_DIR:-/work/sra}"
THREADS="${THREADS:-8}"
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/outputs}"
DEST="s3://${BUCKET}/raw_reads/${SAMPLE}"

if [[ ! "${SAMPLE}" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ || "${SAMPLE}" == *__* ]]; then
  echo "ERROR: sample '${SAMPLE}' must be letters, digits, '_' or '-', at most 64, with no '__'" >&2
  exit "${EXIT_BAD_INPUT}"
fi
read -r -a runs <<< "${SRA_RUNS//,/ }"
if [ "${#runs[@]}" -lt 1 ] || [ "${#runs[@]}" -gt "${MAX_RUNS}" ]; then
  echo "ERROR: give 1 to ${MAX_RUNS} run accessions, got ${#runs[@]}" >&2
  exit "${EXIT_BAD_INPUT}"
fi
declare -A seen=()
for acc in "${runs[@]}"; do
  if [[ ! "${acc}" =~ ^[SED]RR[0-9]{6,10}$ ]]; then
    echo "ERROR: '${acc}' is not a run accession like SRR12046049" >&2
    exit "${EXIT_BAD_INPUT}"
  fi
  if [ -n "${seen[${acc}]-}" ]; then
    echo "ERROR: ${acc} is listed twice" >&2
    exit "${EXIT_BAD_INPUT}"
  fi
  seen["${acc}"]=1
done
expected_marker="$(printf '%s\n' "${runs[@]}")"

mkdir -p "${OUTPUT_DIR}"
# The FASTQ count and total bytes in the sample folder, for the poller to record.
write_outputs() {
  local listing count bytes
  listing="$(aws s3 ls "${DEST}/" 2>/dev/null | grep -E '\.fastq(\.gz)?$' || true)"
  count="$(printf '%s\n' "${listing}" | grep -c . || true)"
  bytes="$(printf '%s\n' "${listing}" | awk '{s += $3} END {print s + 0}')"
  echo "${count}" > "${OUTPUT_DIR}/fastq_count"
  echo "${bytes}" > "${OUTPUT_DIR}/total_bytes"
  echo "raw_reads/${SAMPLE}/: ${count} FASTQs, ${bytes} bytes"
}

# A folder already holding these runs is done; one holding anything else isn't ours to touch.
existing="$(aws s3 ls "${DEST}/" 2>/dev/null || true)"
if printf '%s\n' "${existing}" | grep -qE "[[:space:]]${MARKER}\$"; then
  if [ "$(aws s3 cp "${DEST}/${MARKER}" - 2>/dev/null)" == "${expected_marker}" ]; then
    echo "Already downloaded: ${DEST}/ holds ${runs[*]}"
    write_outputs
    exit 0
  fi
  echo "ERROR: ${DEST}/ already holds FASTQs from other SRA runs" >&2
  exit "${EXIT_FOLDER_TAKEN}"
fi
if printf '%s\n' "${existing}" | grep -qE '\.fastq(\.gz)?$'; then
  echo "ERROR: ${DEST}/ already holds FASTQs; choose another sample name" >&2
  exit "${EXIT_FOLDER_TAKEN}"
fi

# The most common read length among the first reads of a FASTQ.
read_length() {
  head -n "$((SAMPLED_READS * 4))" "$1" \
    | awk 'NR % 4 == 2 {n[length($0)]++} END {for (l in n) if (n[l] > best) {best = n[l]; len = l} print len + 0}'
}

out="${WORK_DIR}/out"
rm -rf "${out}"
mkdir -p "${out}"
lane=0
for acc in "${runs[@]}"; do
  lane=$((lane + 1))
  dump="${WORK_DIR}/${acc}"
  rm -rf "${dump}"
  mkdir -p "${dump}"
  echo "Downloading ${acc} (lane L00${lane})..."
  if ! prefetch "${acc}" --max-size u --output-directory "${dump}"; then
    echo "ERROR: couldn't download ${acc} from SRA (unknown, withdrawn or not public?)" >&2
    exit "${EXIT_DOWNLOAD_FAILED}"
  fi
  if ! fasterq-dump "${dump}/${acc}" --split-files --include-technical \
      --threads "${THREADS}" --outdir "${dump}/fastq" --temp "${dump}/tmp"; then
    echo "ERROR: couldn't convert ${acc} to FASTQ" >&2
    exit "${EXIT_DOWNLOAD_FAILED}"
  fi

  declare -A role=()
  r1="" r2="" index=()
  shopt -s nullglob
  dumped=("${dump}/fastq/${acc}"*.fastq)
  shopt -u nullglob
  for fastq in "${dumped[@]}"; do
    len="$(read_length "${fastq}")"
    if [ "${len}" -ge 26 ] && [ "${len}" -le 28 ]; then
      [ -z "${r1}" ] || { echo "ERROR: ${acc} has two ${len}-bp reads; can't tell which is the barcode read" >&2; exit "${EXIT_READS_UNUSABLE}"; }
      r1="${fastq}"
    elif [ "${len}" -ge 6 ] && [ "${len}" -le 12 ]; then
      index+=("${fastq}")
    elif [ "${len}" -ge 50 ]; then
      [ -z "${r2}" ] || { echo "ERROR: ${acc} has two long reads and no 26-28 bp barcode read; it doesn't look like 10x data" >&2; exit "${EXIT_READS_UNUSABLE}"; }
      r2="${fastq}"
    else
      echo "ERROR: ${acc} has a ${len}-bp read that isn't a 10x barcode, index or cDNA read" >&2
      exit "${EXIT_READS_UNUSABLE}"
    fi
  done
  if [ -z "${r1}" ] || [ -z "${r2}" ]; then
    echo "ERROR: ${acc}'s reads don't include the 10x barcode read (26-28 bp) and the cDNA read; it may have been submitted as a BAM" >&2
    exit "${EXIT_READS_UNUSABLE}"
  fi
  if [ "${#index[@]}" -gt 2 ]; then
    echo "ERROR: ${acc} has more than two index reads" >&2
    exit "${EXIT_READS_UNUSABLE}"
  fi
  role["${r1}"]=R1
  role["${r2}"]=R2
  n=0
  for fastq in "${index[@]}"; do n=$((n + 1)); role["${fastq}"]="I${n}"; done

  for fastq in "${!role[@]}"; do
    pigz -p "${THREADS}" -c "${fastq}" > "${out}/${SAMPLE}_S1_L00${lane}_${role[${fastq}]}_001.fastq.gz"
  done
  rm -rf "${dump}"
  unset role
done

fastq-sample-prefix "${out}" >/dev/null || exit "${EXIT_BAD_FASTQ_NAMES}"

for fastq in "${out}"/*.fastq.gz; do
  aws s3 cp --only-show-errors "${fastq}" "${DEST}/${fastq##*/}"
done
# Written last: its presence means every FASTQ above is in place.
printf '%s\n' "${runs[@]}" | aws s3 cp --only-show-errors - "${DEST}/${MARKER}"
rm -rf "${out}"
write_outputs
