#!/usr/bin/env bash
# Download a run's genome version from Bloom with bloomctl, signed in as the pipeline, for
# cellranger mkref: SHARED_RUNS/RUN_ID.genome/genome/genome.fa and genes.gtf, each checked against
# the SHA-256 and size Bloom recorded, and VERSION with the exact version. The genome folder sits
# next to the run's folder, which the stage step clears of an earlier run's files.
#
# Env: GENOME (<genome>.v<N>), RUN_ID (the run's folder), and bloomctl's credentials file at
# BLOOM_CREDENTIALS (default /etc/bloom/credentials.txt). The credentials are copied into the
# pod's own /tmp, never onto the shared disk. Running it again keeps files already checked.
# Exit codes: 0 downloaded, 6 bad input, 16 no credentials, otherwise bloomctl's own
#      (3 the version isn't available or a file doesn't match Bloom's record, 1 a network or
#      storage error, 2 a usage error).
set -euo pipefail

readonly EXIT_BAD_INPUT=6
readonly EXIT_NO_CREDENTIALS=16
readonly SHARED_RUNS="${SHARED_RUNS:-/shared/runs}"
# A genome name (lowercase, at most 64) and its version number, as the run records it.
readonly GENOME_VERSION_RULE='^[a-z0-9][a-z0-9._-]{0,63}\.v[1-9][0-9]*$'
credentials="${BLOOM_CREDENTIALS:-/etc/bloom/credentials.txt}"

if [[ ! "${GENOME:-}" =~ ${GENOME_VERSION_RULE} ]]; then
  echo "ERROR: GENOME must be a genome version like athaliana_col0.v2, not '${GENOME:-}'" >&2
  exit "${EXIT_BAD_INPUT}"
fi
case "${RUN_ID:-}" in
  ""|.|..|*/*) echo "ERROR: bad run id '${RUN_ID:-}'" >&2; exit "${EXIT_BAD_INPUT}" ;;
esac
if [ ! -s "${credentials}" ]; then
  echo "ERROR: no Bloom credentials at ${credentials}, so the genome can't be downloaded" >&2
  exit "${EXIT_NO_CREDENTIALS}"
fi

HOME="$(mktemp -d)"
export HOME
mkdir -p "${HOME}/.bloom"
install -m 600 "${credentials}" "${HOME}/.bloom/credentials.txt"

dest="${SHARED_RUNS}/${RUN_ID}.genome/genome"
mkdir -p "${dest}"
echo "Downloading ${GENOME} from Bloom into ${dest}"
exec bloomctl genome download "${GENOME}" --to "${dest}" --unzip --version-file "${dest}/VERSION"
