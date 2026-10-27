#!/usr/bin/env bash
# Print the FASTQ sample prefix(es) in a folder, comma-separated, for `cellranger count --sample`.
#
# Files must follow Illumina's naming, <prefix>_S<n>_L<lane>_<read>_001.fastq[.gz], where <read>
# is R1 (barcode and UMI), R2 (cDNA), or an optional index read I1/I2. The prefix is everything
# before that ending and can be anything, so files needn't be named after the sample folder.
# Every lane of every prefix needs both R1 and R2.
#
# Usage: fastq-sample-prefix <folder>
# Exit codes: 0 printed, 7 a file is misnamed or a lane lacks R1 or R2.
set -euo pipefail

readonly EXIT_BAD_FASTQ_NAMES=7
readonly NAME_RULE='<name>_S1_L001_R1_001.fastq.gz and <name>_S1_L001_R2_001.fastq.gz (or .fastq)'
readonly PATTERN='^(.+)_S[0-9]+_L([0-9]{3})_(R1|R2|I1|I2)_001\.fastq(\.gz)?$'

dir="${1:?usage: fastq-sample-prefix <folder>}"

shopt -s nullglob
files=("${dir}"/*.fastq.gz "${dir}"/*.fastq)
shopt -u nullglob

declare -A reads=()
bad=()
for path in "${files[@]}"; do
  name="${path##*/}"
  if [[ "${name}" =~ ${PATTERN} ]]; then
    reads["${BASH_REMATCH[1]}|${BASH_REMATCH[2]}|${BASH_REMATCH[3]}"]=1
  else
    bad+=("${name}")
  fi
done

if [ "${#bad[@]}" -gt 0 ]; then
  echo "ERROR: these FASTQs aren't named like ${NAME_RULE}: ${bad[*]}" >&2
  exit "${EXIT_BAD_FASTQ_NAMES}"
fi
if [ "${#files[@]}" -eq 0 ]; then
  echo "ERROR: no FASTQs in ${dir}" >&2
  exit "${EXIT_BAD_FASTQ_NAMES}"
fi

prefixes=()
missing=()
while IFS='|' read -r prefix lane; do
  [[ " ${prefixes[*]-} " == *" ${prefix} "* ]] || prefixes+=("${prefix}")
  for read_type in R1 R2; do
    [ -n "${reads["${prefix}|${lane}|${read_type}"]-}" ] || missing+=("${prefix}_L${lane}_${read_type}")
  done
done < <(printf '%s\n' "${!reads[@]}" | cut -d'|' -f1,2 | sort -u)

if [ "${#missing[@]}" -gt 0 ]; then
  echo "ERROR: every lane needs an R1 (barcode and UMI) and an R2 (cDNA); missing: ${missing[*]}" >&2
  exit "${EXIT_BAD_FASTQ_NAMES}"
fi

IFS=','
echo "${prefixes[*]}"
