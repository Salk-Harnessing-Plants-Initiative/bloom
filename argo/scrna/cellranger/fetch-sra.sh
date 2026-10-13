#!/usr/bin/env bash
# Download one sample's SRA runs into the run's folder on the shared disk
# (DEST_DIR, /shared/runs/<run-id>/fastq/<sample>/) as Illumina-named .fastq.gz files.
#
# Each run becomes one lane, in the order given. Nothing is written to S3, and no uncompressed
# FASTQ is written anywhere:
#   - ENA first: its ready-made .fastq.gz files are used when their first reads include both
#     the 10x barcode read and the cDNA read. Each is downloaded and checked against ENA's MD5.
#   - Otherwise, or if ENA can't be reached or a download doesn't check out, the run is
#     converted from SRA: prefetch's .sra (compressed) is streamed through fastq-dump, split by
#     read and compressed by pigz as it goes.
# Reads are told apart by length: 6-12 bp is an index read (I1, then I2); of the two longer
# reads, a 26-28 bp one is R1 (barcode and UMI) and the other R2 (cDNA). When both are longer
# than 28 bp (R1 left untrimmed, e.g. 2x150), R1 is the one whose first 16 bases are on a 10x
# barcode list.
#
# The run's folder is claimed with claim-run-dir first ("sra <runs>"), so a folder left by a run
# on other reads is set aside. Each finished lane is recorded (DEST_DIR/.L00<n>), so a retry
# resumes; .sra-runs, written last, means every lane is there.
#
# Env: SAMPLE, SRA_RUNS (accessions separated by commas, spaces or newlines), RUN_DIR,
#      DEST_DIR (default RUN_DIR/fastq/SAMPLE), THREADS, BARCODE_DIR, ENA_API.
# Exit codes: 0 done or already there, 6 a bad sample name or accession list, 7 misnamed files
#      (fastq-sample-prefix), 10 a run couldn't be downloaded, 11 a run lacks the barcode or
#      cDNA read.
set -euo pipefail

readonly EXIT_BAD_INPUT=6
readonly EXIT_BAD_FASTQ_NAMES=7
readonly EXIT_TRANSFER_FAILED=10
readonly EXIT_READS_UNUSABLE=11
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
# How long ENA may take to answer, and to send a file's first reads.
readonly ENA_TIMEOUT=60

SAMPLE="${SAMPLE:-}"
SRA_RUNS="${SRA_RUNS:-}"
RUN_DIR="${RUN_DIR:-}"
DEST_DIR="${DEST_DIR:-${RUN_DIR}/fastq/${SAMPLE}}"
THREADS="${THREADS:-8}"
BARCODE_DIR="${BARCODE_DIR:-${CELLRANGER_HOME:-/opt/cellranger}/lib/python/cellranger/barcodes}"
ENA_API="${ENA_API:-https://www.ebi.ac.uk/ena/portal/api/filereport}"

if [[ ! "${SAMPLE}" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ || "${SAMPLE}" == *__* ]]; then
  echo "ERROR: sample '${SAMPLE}' must be letters, digits, '_' or '-', at most 64, with no '__'" >&2
  exit "${EXIT_BAD_INPUT}"
fi
if [ -z "${RUN_DIR}" ]; then
  echo "ERROR: RUN_DIR is required" >&2
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

claim-run-dir "${RUN_DIR}" "sra ${runs[*]}" || exit "${EXIT_BAD_INPUT}"
mkdir -p "${DEST_DIR}"
if [ "$(cat "${DEST_DIR}/${MARKER}" 2>/dev/null)" = "$(printf '%s\n' "${runs[@]}")" ]; then
  echo "Already downloaded: ${DEST_DIR}/ holds ${runs[*]}"
  exit 0
fi
# Where prefetch keeps each run's .sra while it is converted; removed once the run is done.
sra_dir="${RUN_DIR}/sra"

# A FASTQ's first reads, from a .fastq.gz file or URL, as plain text on stdout.
first_reads() {
  if [[ "$1" == https://* ]]; then
    { curl -fsS --max-time "${ENA_TIMEOUT}" "$1" | gzip -dc | head -n "$((SAMPLED_READS * 4))"; } 2>/dev/null || true
  else
    { gzip -dc "$1" | head -n "$((SAMPLED_READS * 4))"; } 2>/dev/null || true
  fi
}

# The most common read length in sampled reads.
read_length() {
  awk 'NR % 4 == 2 {n[length($0)]++} END {for (l in n) if (n[l] > best) {best = n[l]; len = l} print len + 0}' "$1"
}

# Percent of sampled reads whose first 16 bases are on one of the 10x barcode lists (the best list).
barcode_share() {
  local barcodes best=0 list share
  barcodes="$(mktemp)"
  awk -v n="${BARCODE_LEN}" 'NR % 4 == 2 {print substr($0, 1, n)}' "$1" > "${barcodes}"
  for list in "${BARCODE_LISTS[@]}"; do
    [ -f "${BARCODE_DIR}/${list}" ] || continue
    share="$(gzip -dcf "${BARCODE_DIR}/${list}" | awk 'NR == FNR {n++; want[$0]++; next}
      ($0 in want) {hit += want[$0]; delete want[$0]} END {print (n ? int(100 * hit / n) : 0)}' "${barcodes}" -)"
    [ "${share}" -gt "${best}" ] && best="${share}"
  done
  rm -f "${barcodes}"
  echo "${best}"
}

# Names each of a run's reads R1, R2, I1 or I2 from its sampled reads. Takes the accession and
# then the reads' sample files; sets `role` (sample file -> role). Returns 1 with the reason in
# `why` when the reads aren't one 10x library's.
declare -A role=()
why=""
assign_roles() {
  local acc="$1" sampled len a b la lb sa sb r1 r2 n
  shift
  local -a reads=() index=()
  local -A len_of=()
  role=()
  why=""
  for sampled in "$@"; do
    len="$(read_length "${sampled}")"
    len_of["${sampled}"]="${len}"
    if [ "${len}" -ge "${INDEX_MIN_LEN}" ] && [ "${len}" -le "${INDEX_MAX_LEN}" ]; then
      index+=("${sampled}")
    elif [ "${len}" -ge "${R1_MIN_LEN}" ]; then
      reads+=("${sampled}")
    else
      why="${acc} has a ${len}-bp read that isn't a 10x barcode, index or cDNA read"
      return 1
    fi
  done
  if [ "${#reads[@]}" -lt 2 ]; then
    why="${acc}'s reads don't include both the 10x barcode read and the cDNA read; it may have been submitted as a BAM"
    return 1
  fi
  if [ "${#reads[@]}" -gt 2 ]; then
    why="${acc} has ${#reads[@]} reads longer than an index read; a 10x run has two (barcode and cDNA)"
    return 1
  fi
  if [ "${#index[@]}" -gt 2 ]; then
    why="${acc} has more than two index reads"
    return 1
  fi
  a="${reads[0]}" b="${reads[1]}"
  la="${len_of[${a}]}" lb="${len_of[${b}]}"
  if [ "${la}" -le "${R1_MAX_LEN}" ] && [ "${lb}" -le "${R1_MAX_LEN}" ]; then
    why="${acc} has two ${la}-bp reads and no cDNA read; it doesn't look like 10x data"
    return 1
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
      why="${acc} has ${la}- and ${lb}-bp reads and can't tell which is the 10x barcode read (${sa}% and ${sb}% of barcodes on a 10x list)"
      return 1
    fi
  fi
  role["${r1}"]=R1
  role["${r2}"]=R2
  n=0
  for sampled in "${index[@]}"; do n=$((n + 1)); role["${sampled}"]="I${n}"; done
}

# Downloads a run from ENA as lane `lane` into `part`. Returns 1, having written nothing final,
# when ENA doesn't have usable files for it or a download doesn't check out.
from_ena() {
  local acc="$1" lane="$2" part="$3" report header row urls_col md5_col i url sampled got
  local -a urls=() md5s=() samples=()
  local -A url_of=() md5_of=()
  if ! report="$(curl -fsS --max-time "${ENA_TIMEOUT}" \
      "${ENA_API}?accession=${acc}&result=read_run&fields=fastq_ftp,fastq_md5&format=tsv" 2>&1)"; then
    echo "ENA couldn't be asked about ${acc} ($(head -c 200 <<<"${report}")); converting from SRA"
    return 1
  fi
  header="$(sed -n 1p <<<"${report}")"
  row="$(sed -n 2p <<<"${report}")"
  urls_col="$(tr '\t' '\n' <<<"${header}" | grep -nx fastq_ftp | cut -d: -f1 || true)"
  md5_col="$(tr '\t' '\n' <<<"${header}" | grep -nx fastq_md5 | cut -d: -f1 || true)"
  if [ -z "${row}" ] || [ -z "${urls_col}" ] || [ -z "${md5_col}" ]; then
    echo "ENA has no FASTQs for ${acc}; converting from SRA"
    return 1
  fi
  IFS=';' read -ra urls <<<"$(cut -f "${urls_col}" <<<"${row}")"
  IFS=';' read -ra md5s <<<"$(cut -f "${md5_col}" <<<"${row}")"
  if [ "${#urls[@]}" -lt 2 ] || [ "${#urls[@]}" -ne "${#md5s[@]}" ]; then
    echo "ENA has ${#urls[@]} FASTQ(s) for ${acc}, not a barcode and a cDNA read; converting from SRA"
    return 1
  fi
  for i in "${!urls[@]}"; do
    url="https://${urls[${i}]#*://}"
    sampled="${part}/head-${i}"
    first_reads "${url}" > "${sampled}"
    if [ ! -s "${sampled}" ]; then
      echo "Couldn't read the start of ${url}; converting ${acc} from SRA"
      return 1
    fi
    url_of["${sampled}"]="${url}"
    md5_of["${sampled}"]="${md5s[${i}]}"
    samples+=("${sampled}")
  done
  if ! assign_roles "${acc}" "${samples[@]}"; then
    echo "ENA's FASTQs for ${acc} aren't usable as they are (${why}); converting from SRA"
    return 1
  fi
  for sampled in "${samples[@]}"; do
    url="${url_of[${sampled}]}"
    echo "Downloading ${url} as ${role[${sampled}]}..."
    if ! curl -fsS --retry 3 -o "${part}/${role[${sampled}]}.fastq.gz" "${url}"; then
      echo "Couldn't download ${url}; converting ${acc} from SRA"
      return 1
    fi
    got="$(md5sum "${part}/${role[${sampled}]}.fastq.gz" | cut -d' ' -f1)"
    if [ "${got}" != "${md5_of[${sampled}]}" ]; then
      echo "${url} doesn't match ENA's MD5 (${got}, not ${md5_of[${sampled}]}); converting ${acc} from SRA"
      return 1
    fi
  done
}

# Converts a run from SRA as lane `lane` into `part`: each read is compressed as it streams, so
# nothing uncompressed is written. Exits 10 or 11 on failure.
from_sra() {
  local acc="$1" part="$2" fastq sampled
  local -a samples=()
  local -A fastq_of=()
  rm -rf "${sra_dir:?}/${acc}"
  mkdir -p "${sra_dir}"
  if ! prefetch "${acc}" --max-size u --output-directory "${sra_dir}"; then
    echo "ERROR: couldn't download ${acc} from SRA (unknown, withdrawn or not public?)" >&2
    exit "${EXIT_TRANSFER_FAILED}"
  fi
  # Technical reads (the 10x barcode read) are kept; each record is named <acc>.<spot>.<read>.
  if ! fastq-dump --split-spot --stdout --defline-seq '@$ac.$si.$ri' --defline-qual '+' \
      "${sra_dir}/${acc}" \
    | awk -v dir="${part}" -v threads="${THREADS}" '
        NR % 4 == 1 {
          n = split(substr($1, 2), name, ".")
          if (n != 3 || name[3] !~ /^[0-9]+$/) { print "bad record name: " $1 > "/dev/stderr"; exit 1 }
          cmd = "pigz -p " threads " > " dir "/read-" name[3] ".fastq.gz"
        }
        { print | cmd }'; then
    echo "ERROR: couldn't convert ${acc} to FASTQ" >&2
    exit "${EXIT_TRANSFER_FAILED}"
  fi
  rm -rf "${sra_dir:?}/${acc}"
  shopt -s nullglob
  for fastq in "${part}"/read-*.fastq.gz; do
    sampled="${fastq%.fastq.gz}.head"
    first_reads "${fastq}" > "${sampled}"
    fastq_of["${sampled}"]="${fastq}"
    samples+=("${sampled}")
  done
  shopt -u nullglob
  if ! assign_roles "${acc}" "${samples[@]}"; then
    echo "ERROR: ${why}" >&2
    exit "${EXIT_READS_UNUSABLE}"
  fi
  for sampled in "${samples[@]}"; do
    mv -- "${fastq_of[${sampled}]}" "${part}/${role[${sampled}]}.fastq.gz"
  done
}

lane=0
for acc in "${runs[@]}"; do
  lane=$((lane + 1))
  done_marker="${DEST_DIR}/.L00${lane}"
  if [ "$(cat "${done_marker}" 2>/dev/null)" = "${acc}" ]; then
    echo "Lane L00${lane} (${acc}) was downloaded by an earlier try"
    continue
  fi
  rm -f -- "${DEST_DIR}/${SAMPLE}_S1_L00${lane}_"*
  part="${DEST_DIR}/.part-L00${lane}"
  rm -rf -- "${part}"
  mkdir -p "${part}"
  echo "Downloading ${acc} (lane L00${lane})..."
  if ! from_ena "${acc}" "${lane}" "${part}"; then
    rm -rf -- "${part}"
    mkdir -p "${part}"
    from_sra "${acc}" "${part}"
  fi
  for fastq in "${part}"/[RI][12].fastq.gz; do
    read_role="${fastq##*/}"
    mv -- "${fastq}" "${DEST_DIR}/${SAMPLE}_S1_L00${lane}_${read_role%.fastq.gz}_001.fastq.gz"
  done
  rm -rf -- "${part}"
  printf '%s\n' "${acc}" > "${done_marker}"
done
rm -rf -- "${sra_dir}"

fastq-sample-prefix "${DEST_DIR}" >/dev/null || exit "${EXIT_BAD_FASTQ_NAMES}"
# Written last: its presence means every lane above is in place.
printf '%s\n' "${runs[@]}" > "${DEST_DIR}/${MARKER}"
echo "${DEST_DIR}/: $(find "${DEST_DIR}" -maxdepth 1 -name '*.fastq.gz' | wc -l | tr -d ' ') FASTQs from ${runs[*]}"
