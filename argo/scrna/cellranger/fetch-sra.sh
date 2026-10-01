#!/usr/bin/env bash
# Download one sample's SRA runs into s3://<bucket>/raw_reads/<sample>/ as Illumina-named FASTQs.
#
# Each run becomes one lane, in the order given. Its reads are downloaded with their technical
# reads, which is where 10x's barcode read is kept, and told apart by length: 6-12 bp is an
# index read (I1, then I2); of the two longer reads, a 26-28 bp one is R1 (barcode and UMI) and
# the other R2 (cDNA). When both are longer than 28 bp (R1 left untrimmed, e.g. 2x150), R1 is
# the one whose first 16 bases are on a 10x barcode list. A folder that already holds these
# runs (its .sra-runs marker matches) is left as it is.
#
# Env: SAMPLE, SRA_RUNS (accessions separated by commas, spaces or newlines), BUCKET, WORK_DIR,
#      THREADS, OUTPUT_DIR (where fastq_count and total_bytes are written for Argo), BARCODE_DIR.
# Exit codes: 0 done or already there, 6 a bad sample name or accession list, 7 misnamed files
#      (fastq-sample-prefix), 10 a run couldn't be downloaded or S3 couldn't be checked, 11 a run
#      lacks the barcode or cDNA read, 12 the folder holds other FASTQs.
set -euo pipefail

readonly EXIT_BAD_INPUT=6
readonly EXIT_BAD_FASTQ_NAMES=7
readonly EXIT_TRANSFER_FAILED=10
readonly EXIT_READS_UNUSABLE=11
readonly EXIT_FOLDER_TAKEN=12
# Lanes are named L001-L009.
readonly MAX_RUNS=9
# Reads sampled from each file to find its read length and barcodes.
readonly SAMPLED_READS=4000
readonly MARKER=".sra-runs"
# Read lengths: an index read, and the shortest R1 (16 bp barcode + 10 bp UMI) and longest
# trimmed R1 (16 + 12).
readonly INDEX_MIN_LEN=6
readonly INDEX_MAX_LEN=12
readonly R1_MIN_LEN=26
readonly R1_MAX_LEN=28
readonly BARCODE_LEN=16
# Percent of sampled reads whose barcode must be on a 10x list for the read to count as R1.
readonly MIN_BARCODE_SHARE=50
# The 10x gene expression barcode lists in the image.
readonly BARCODE_LISTS=(737K-august-2016.txt 3M-february-2018_TRU.txt.gz 3M-3pgex-may-2023_TRU.txt.gz
  3M-5pgex-jan-2023.txt.gz 737K-arc-v1.txt.gz)

SAMPLE="${SAMPLE:-}"
SRA_RUNS="${SRA_RUNS:-}"
BUCKET="${BUCKET:-bloomv2-workflows}"
WORK_DIR="${WORK_DIR:-/work/sra}"
THREADS="${THREADS:-8}"
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/outputs}"
BARCODE_DIR="${BARCODE_DIR:-${CELLRANGER_HOME:-/opt/cellranger}/lib/python/cellranger/barcodes}"
DEST="s3://${BUCKET}/raw_reads/${SAMPLE}"

if [[ ! "${SAMPLE}" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ || "${SAMPLE}" == *__* ]]; then
  echo "ERROR: sample '${SAMPLE}' must be letters, digits, '_' or '-', at most 64, with no '__'" >&2
  exit "${EXIT_BAD_INPUT}"
fi
mapfile -t runs < <(printf '%s\n' "${SRA_RUNS}" | tr -s ', \t\r' '\n' | sed '/^$/d')
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
# The sample folder's listing. Fails if S3 couldn't be asked: only exit 1 with no output and no
# error means the folder is empty.
list_folder() {
  local out err rc=0
  err="$(mktemp)"
  out="$(aws s3 ls "${DEST}/" 2>"${err}")" || rc=$?
  if [ "${rc}" -eq 0 ] || { [ "${rc}" -eq 1 ] && [ -z "${out}" ] && [ ! -s "${err}" ]; }; then
    rm -f "${err}"
    printf '%s\n' "${out}"
    return 0
  fi
  echo "ERROR: couldn't check ${DEST}/ (aws exit ${rc}): $(head -c 300 "${err}")" >&2
  rm -f "${err}"
  return 1
}

# The FASTQ count and total bytes in the sample folder, for the poller to record.
write_outputs() {
  local listing count bytes
  listing="$(list_folder)" || exit "${EXIT_TRANSFER_FAILED}"
  listing="$(printf '%s\n' "${listing}" | grep -E '\.fastq(\.gz)?$' || true)"
  count="$(printf '%s\n' "${listing}" | grep -c . || true)"
  bytes="$(printf '%s\n' "${listing}" | awk '{s += $3} END {print s + 0}')"
  echo "${count}" > "${OUTPUT_DIR}/fastq_count"
  echo "${bytes}" > "${OUTPUT_DIR}/total_bytes"
  echo "raw_reads/${SAMPLE}/: ${count} FASTQs, ${bytes} bytes"
}

# A folder already holding these runs is done; one holding anything else isn't ours to touch.
existing="$(list_folder)" || exit "${EXIT_TRANSFER_FAILED}"
if printf '%s\n' "${existing}" | grep -qE "[[:space:]]${MARKER}\$"; then
  if ! marker="$(aws s3 cp "${DEST}/${MARKER}" -)"; then
    echo "ERROR: couldn't read ${DEST}/${MARKER}; nothing was changed" >&2
    exit "${EXIT_TRANSFER_FAILED}"
  fi
  if [ "${marker}" == "${expected_marker}" ]; then
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

# Percent of a FASTQ's first reads whose first 16 bases are on one of the 10x barcode lists (the best list).
barcode_share() {
  local sampled best=0 list share
  sampled="$(mktemp)"
  head -n "$((SAMPLED_READS * 4))" "$1" | awk -v n="${BARCODE_LEN}" 'NR % 4 == 2 {print substr($0, 1, n)}' > "${sampled}"
  for list in "${BARCODE_LISTS[@]}"; do
    [ -f "${BARCODE_DIR}/${list}" ] || continue
    share="$(gzip -dcf "${BARCODE_DIR}/${list}" | awk 'NR == FNR {n++; want[$0]++; next}
      ($0 in want) {hit += want[$0]; delete want[$0]} END {print (n ? int(100 * hit / n) : 0)}' "${sampled}" -)"
    [ "${share}" -gt "${best}" ] && best="${share}"
  done
  rm -f "${sampled}"
  echo "${best}"
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
    exit "${EXIT_TRANSFER_FAILED}"
  fi
  if ! fasterq-dump "${dump}/${acc}" --split-files --include-technical \
      --threads "${THREADS}" --outdir "${dump}/fastq" --temp "${dump}/tmp"; then
    echo "ERROR: couldn't convert ${acc} to FASTQ" >&2
    exit "${EXIT_TRANSFER_FAILED}"
  fi

  declare -A role=() len_of=()
  reads=() index=()
  shopt -s nullglob
  dumped=("${dump}/fastq/${acc}"*.fastq)
  shopt -u nullglob
  for fastq in "${dumped[@]}"; do
    len="$(read_length "${fastq}")"
    len_of["${fastq}"]="${len}"
    if [ "${len}" -ge "${INDEX_MIN_LEN}" ] && [ "${len}" -le "${INDEX_MAX_LEN}" ]; then
      index+=("${fastq}")
    elif [ "${len}" -ge "${R1_MIN_LEN}" ]; then
      reads+=("${fastq}")
    else
      echo "ERROR: ${acc} has a ${len}-bp read that isn't a 10x barcode, index or cDNA read" >&2
      exit "${EXIT_READS_UNUSABLE}"
    fi
  done
  if [ "${#reads[@]}" -lt 2 ]; then
    echo "ERROR: ${acc}'s reads don't include both the 10x barcode read and the cDNA read; it may have been submitted as a BAM" >&2
    exit "${EXIT_READS_UNUSABLE}"
  fi
  if [ "${#reads[@]}" -gt 2 ]; then
    echo "ERROR: ${acc} has ${#reads[@]} reads longer than an index read; a 10x run has two (barcode and cDNA)" >&2
    exit "${EXIT_READS_UNUSABLE}"
  fi
  if [ "${#index[@]}" -gt 2 ]; then
    echo "ERROR: ${acc} has more than two index reads" >&2
    exit "${EXIT_READS_UNUSABLE}"
  fi
  a="${reads[0]}" b="${reads[1]}"
  la="${len_of[${a}]}" lb="${len_of[${b}]}"
  if [ "${la}" -le "${R1_MAX_LEN}" ] && [ "${lb}" -le "${R1_MAX_LEN}" ]; then
    echo "ERROR: ${acc} has two ${la}-bp reads and no cDNA read; it doesn't look like 10x data" >&2
    exit "${EXIT_READS_UNUSABLE}"
  elif [ "${la}" -le "${R1_MAX_LEN}" ]; then
    r1="${a}" r2="${b}"
  elif [ "${lb}" -le "${R1_MAX_LEN}" ]; then
    r1="${b}" r2="${a}"
  else
    # Both longer than a trimmed R1: the barcode read is the one whose first 16 bases are on a list.
    sa="$(barcode_share "${a}")" sb="$(barcode_share "${b}")"
    echo "${acc}: ${la}-bp read ${sa}% on a 10x barcode list, ${lb}-bp read ${sb}%"
    if [ "${sa}" -ge "${MIN_BARCODE_SHARE}" ] && [ "${sb}" -lt "${MIN_BARCODE_SHARE}" ]; then
      r1="${a}" r2="${b}"
    elif [ "${sb}" -ge "${MIN_BARCODE_SHARE}" ] && [ "${sa}" -lt "${MIN_BARCODE_SHARE}" ]; then
      r1="${b}" r2="${a}"
    else
      echo "ERROR: ${acc} has ${la}- and ${lb}-bp reads and can't tell which is the 10x barcode read (${sa}% and ${sb}% of barcodes on a 10x list)" >&2
      exit "${EXIT_READS_UNUSABLE}"
    fi
  fi
  role["${r1}"]=R1
  role["${r2}"]=R2
  n=0
  for fastq in "${index[@]}"; do n=$((n + 1)); role["${fastq}"]="I${n}"; done

  for fastq in "${!role[@]}"; do
    pigz -p "${THREADS}" -c "${fastq}" > "${out}/${SAMPLE}_S1_L00${lane}_${role[${fastq}]}_001.fastq.gz"
  done
  rm -rf "${dump}"
done

fastq-sample-prefix "${out}" >/dev/null || exit "${EXIT_BAD_FASTQ_NAMES}"

for fastq in "${out}"/*.fastq.gz; do
  aws s3 cp --only-show-errors "${fastq}" "${DEST}/${fastq##*/}"
done
# Written last: its presence means every FASTQ above is in place.
printf '%s\n' "${runs[@]}" | aws s3 cp --only-show-errors - "${DEST}/${MARKER}"
rm -rf "${out}"
write_outputs
