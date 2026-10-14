#!/usr/bin/env bash
# Make a run's folder on the shared disk this run's own, before its reads are written there.
#
# The folder is named by the run key, which a new run can reuse after a failed one. RUN_DIR/.inputs
# records what the folder was built from: the same inputs (a retry) keep everything, so the step
# resumes; other inputs set the folder aside first. The old FASTQs are deleted, and the rest (its
# logs and Cell Ranger's errors) is moved to RUN_DIR.failed-<time>/.
#
# Usage: claim-run-dir <run dir> <inputs, as one line>
# Exit codes: 0 claimed, 6 bad arguments.
set -euo pipefail

readonly EXIT_BAD_INPUT=6

run_dir="${1:-}"
inputs="${2:-}"
if [ -z "${run_dir}" ] || [ -z "${inputs}" ] || [[ "${inputs}" == *$'\n'* ]]; then
  echo "ERROR: usage: claim-run-dir <run dir> <inputs, as one line>" >&2
  exit "${EXIT_BAD_INPUT}"
fi

mkdir -p "${run_dir}"
if [ "$(cat "${run_dir}/.inputs" 2>/dev/null)" = "${inputs}" ]; then
  exit 0
fi
shopt -s nullglob dotglob
leftover=()
for entry in "${run_dir}"/*; do
  [ "${entry##*/}" = .inputs ] || leftover+=("${entry}")
done
shopt -u nullglob dotglob
if [ "${#leftover[@]}" -gt 0 ]; then
  rm -rf -- "${run_dir}/fastq"
  aside="${run_dir}.failed-$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "${aside}"
  for entry in "${leftover[@]}"; do
    [ -e "${entry}" ] && mv -- "${entry}" "${aside}/"
  done
  echo "Set aside an earlier run's folder (other inputs) in ${aside}; its FASTQs were deleted"
fi
printf '%s\n' "${inputs}" > "${run_dir}/.inputs"
