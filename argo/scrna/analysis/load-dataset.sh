#!/usr/bin/env bash
# Load a run's final .h5ad into Bloom as a dataset, with bloomctl, signed in as the pipeline.
#
# Env: SAMPLE, RUN_ID (the run's folder), DATASET_NAME, SPECIES_NAME (the species' common name),
# BLOOM_RUN_ID (the rnaseq_runs id; empty for a run started by hand), and bloomctl's credentials
# file at BLOOM_CREDENTIALS (default /etc/bloom/credentials.txt).
# A run that names no dataset (DATASET_NAME empty) loads nothing, and the step succeeds.
# A run from Bloom loads under DATASET_NAME, or N_v2, N_v3, ... when it's taken, tagged with
# BLOOM_RUN_ID, and writes the dataset's id to OUTPUT_DIR/dataset-id for the status poller.
# The credentials are copied into the pod's own /tmp, never onto the shared disk. bloomctl's
# resume state (~/.bloom/scrna-uploads) is the run's folder on the shared disk, so a retried
# step continues the load; cleanup removes it with the rest of the run's folder.
# Exit codes: 0 loaded or nothing to load, 6 bad input or no .h5ad, 16 no credentials,
#      otherwise bloomctl's own (1 a refused or failed load, 2 a usage error).
set -euo pipefail

readonly EXIT_BAD_INPUT=6
readonly EXIT_NO_CREDENTIALS=16
readonly SHARED_RUNS="${SHARED_RUNS:-/shared/runs}"
readonly OUTPUT_DIR="${OUTPUT_DIR:-/tmp/outputs}"
credentials="${BLOOM_CREDENTIALS:-/etc/bloom/credentials.txt}"

mkdir -p "${OUTPUT_DIR}"
: > "${OUTPUT_DIR}/dataset-id"
if [ -z "${DATASET_NAME:-}" ]; then
  echo "This run names no dataset, so nothing is loaded into Bloom"
  exit 0
fi
if [ -z "${SAMPLE:-}" ] || [ -z "${RUN_ID:-}" ] || [ -z "${SPECIES_NAME:-}" ]; then
  echo "ERROR: SAMPLE, RUN_ID and SPECIES_NAME are required to load a dataset" >&2
  exit "${EXIT_BAD_INPUT}"
fi
if [ ! -s "${credentials}" ]; then
  echo "ERROR: no Bloom credentials at ${credentials}, so the dataset can't be loaded" >&2
  exit "${EXIT_NO_CREDENTIALS}"
fi
h5ad="${SHARED_RUNS}/${RUN_ID}/h5ad/${SAMPLE}.h5ad"
if [ ! -f "${h5ad}" ]; then
  echo "ERROR: no final .h5ad at ${h5ad}" >&2
  exit "${EXIT_BAD_INPUT}"
fi

HOME="$(mktemp -d)"
export HOME
mkdir -p "${HOME}/.bloom"
install -m 600 "${credentials}" "${HOME}/.bloom/credentials.txt"
state="${SHARED_RUNS}/${RUN_ID}/bloomctl-uploads"
mkdir -p "${state}"
chmod 700 "${state}"
ln -s "${state}" "${HOME}/.bloom/scrna-uploads"

if [ -z "${BLOOM_RUN_ID:-}" ]; then
  echo "Loading ${h5ad} into Bloom as '${DATASET_NAME}' (${SPECIES_NAME})"
  exec bloomctl scrna hdf5 upload "${h5ad}" --name "${DATASET_NAME}" --species "${SPECIES_NAME}" \
    --annotation leiden --create --yes
fi

name="$(bloom-dataset pick --name "${DATASET_NAME}" --species "${SPECIES_NAME}" --run-id "${BLOOM_RUN_ID}")"
[ "${name}" = "${DATASET_NAME}" ] || echo "'${DATASET_NAME}' is taken, so this run loads as '${name}'"
echo "Loading ${h5ad} into Bloom as '${name}' (${SPECIES_NAME}) for run ${BLOOM_RUN_ID}"
bloomctl scrna hdf5 upload "${h5ad}" --name "${name}" --species "${SPECIES_NAME}" \
  --annotation leiden --create --yes --run-id "${BLOOM_RUN_ID}"
bloom-dataset id --name "${name}" --species "${SPECIES_NAME}" --run-id "${BLOOM_RUN_ID}" \
  > "${OUTPUT_DIR}/dataset-id"
echo "Loaded as dataset $(cat "${OUTPUT_DIR}/dataset-id")"
