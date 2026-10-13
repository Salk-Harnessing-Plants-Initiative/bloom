#!/usr/bin/env bash
# Copy an S3 folder run's FASTQs onto the run's folder on the shared disk.
#
# The reads come from FASTQ_URL: exactly the files listed in FASTQ_FILES (a JSON list of
# {name, size, etag}, recorded when the run was started), and nothing is written to S3. If the
# folder holds no FASTQ yet, it waits up to WAIT_SECONDS in case an upload is still finishing.
# A file added, removed, resized or replaced since the run was started fails the step, so the
# run never uses other reads than it was started on. The folder is read anonymously; if that
# listing is refused, it's read signed as Bloom's AWS user (bloomv2-workflows-job,
# AWS_ACCESS_KEY_ID), for a folder a scientist shared with it. An SRA run's reads are downloaded
# by fetch-sra instead, and this step is skipped.
#
# The run's folder (RUN_DIR) is claimed with claim-run-dir: a retry on the same inputs resumes,
# and a folder left by a run on other inputs is set aside first.
#
# Env: SAMPLE, DEST_DIR, RUN_DIR (default: DEST_DIR's grandparent), FASTQ_URL, FASTQ_FILES.
#      WAIT_SECONDS and POLL_SECONDS tune the wait. AWS_* sign the reads of a shared folder.
# Exit codes: 0 copied, 4 no FASTQs, 6 bad input, 7 misnamed FASTQs (fastq-sample-prefix),
#      8 the folder changed since the run was started (or while it was being copied),
#      9 the recorded FASTQs are named for another sample, 10 S3 couldn't be listed or read.
set -euo pipefail

readonly EXIT_NO_FASTQS=4
readonly EXIT_BAD_INPUT=6
readonly EXIT_BAD_FASTQ_NAMES=7
readonly EXIT_FOLDER_CHANGED=8
readonly EXIT_OTHER_SAMPLE=9
readonly EXIT_TRANSFER_FAILED=10
readonly URL_RULE='^s3://([a-z0-9][a-z0-9.-]{1,61}[a-z0-9])/(([A-Za-z0-9!_.*'"'"'()-]+/)+)$'

SAMPLE="${SAMPLE:-}"
DEST_DIR="${DEST_DIR:-}"
RUN_DIR="${RUN_DIR:-$(dirname "$(dirname "${DEST_DIR:-/x/y}")")}"
FASTQ_URL="${FASTQ_URL:-}"
FASTQ_FILES="${FASTQ_FILES:-}"
WAIT_SECONDS="${WAIT_SECONDS:-600}"
POLL_SECONDS="${POLL_SECONDS:-30}"

if [ -z "${SAMPLE}" ] || [ -z "${DEST_DIR}" ] || [ -z "${FASTQ_URL}" ]; then
  echo "ERROR: SAMPLE, DEST_DIR and FASTQ_URL are required" >&2
  exit "${EXIT_BAD_INPUT}"
fi

# What this run's folder is built from, as one line.
inputs="$(FASTQ_URL="${FASTQ_URL}" FASTQ_FILES="${FASTQ_FILES}" stage-fastqs-lib fingerprint)"
claim-run-dir "${RUN_DIR}" "${inputs}" || exit "${EXIT_BAD_INPUT}"
mkdir -p "${DEST_DIR}"

bucket=""
if [[ "${FASTQ_URL}" =~ ${URL_RULE} ]]; then
  bucket="${BASH_REMATCH[1]}"
  prefix_path="${BASH_REMATCH[2]}"
fi
if [ -z "${bucket}" ] || [[ "${FASTQ_URL}" =~ /\.{1,2}/ ]]; then
  echo "ERROR: '${FASTQ_URL}' is not a folder like s3://bucket/folder/" >&2
  exit "${EXIT_BAD_INPUT}"
fi
# The recorded files must be a usable list, all for this run's sample, before anything is copied.
rc=0
problem="$(FASTQ_FILES="${FASTQ_FILES}" SAMPLE="${SAMPLE}" stage-fastqs-lib check-files)" || rc=$?
if [ "${rc}" -eq 3 ]; then
  echo "ERROR: the FASTQs in ${FASTQ_URL} are named for ${problem}, not the run's sample '${SAMPLE}'" >&2
  exit "${EXIT_OTHER_SAMPLE}"
elif [ "${rc}" -ne 0 ]; then
  [ -n "${problem}" ] || problem="FASTQ_FILES must be the run's list of files"
  echo "ERROR: ${problem}" >&2
  exit "${EXIT_BAD_INPUT}"
fi

# How the folder is read: anonymously, as the start API checked it, or signed as Bloom's AWS
# user once an anonymous listing is refused (a folder its owner shared with that user).
read_as=anonymous
folder_aws() {
  if [ "${read_as}" = signed ]; then
    aws "$@"
  else
    aws "$@" --no-sign-request
  fi
}

list_err="$(mktemp)"
deadline=$(( $(date +%s) + WAIT_SECONDS ))
while :; do
  if ! listing="$(folder_aws s3api list-objects-v2 --bucket "${bucket}" --prefix "${prefix_path}" \
      --delimiter / --max-keys 1000 --no-paginate --output json 2>"${list_err}")"; then
    if [ "${read_as}" = anonymous ] && [ -n "${AWS_ACCESS_KEY_ID:-}" ] \
        && grep -qE 'AccessDenied|\(403\)' "${list_err}"; then
      read_as=signed
      echo "${FASTQ_URL} isn't public; reading it as Bloom's AWS user"
      continue
    fi
    echo "ERROR: couldn't list ${FASTQ_URL}: $(head -c 300 "${list_err}")" >&2
    exit "${EXIT_TRANSFER_FAILED}"
  fi
  # On stdin: a large listing would pass the environment's size limit.
  state="$(printf '%s' "${listing}" | FASTQ_FILES="${FASTQ_FILES}" stage-fastqs-lib compare "${prefix_path}")"
  case "${state}" in
    match) break ;;
    changed:*)
      echo "ERROR: ${FASTQ_URL} changed after the run was started (${state#changed: })" >&2
      exit "${EXIT_FOLDER_CHANGED}"
      ;;
  esac
  if [ "$(date +%s)" -ge "${deadline}" ]; then
    if [ "${state}" = empty ]; then
      echo "ERROR: no FASTQs at ${FASTQ_URL} after waiting ${WAIT_SECONDS}s" >&2
      exit "${EXIT_NO_FASTQS}"
    fi
    echo "ERROR: ${FASTQ_URL} changed after the run was started (missing ${state#partial: })" >&2
    exit "${EXIT_FOLDER_CHANGED}"
  fi
  echo "Waiting for ${FASTQ_URL} (${state}); checking again in ${POLL_SECONDS}s"
  sleep "${POLL_SECONDS}"
done

# Each file is copied, then checked: the copy has the recorded size, and the object still has
# the recorded ETag, so a file replaced while it was being copied isn't used.
copied=0
while IFS=$'\t' read -r name size etag; do
  dest="${DEST_DIR}/${name}"
  folder_aws s3 cp --only-show-errors "${FASTQ_URL}${name}" "${dest}" \
    || exit "${EXIT_TRANSFER_FAILED}"
  got="$(stat -c %s "${dest}")"
  if [ "${got}" != "${size}" ]; then
    rm -f -- "${dest}"
    echo "ERROR: ${name} copied as ${got} bytes, not the ${size} recorded; it changed while it was being copied" >&2
    exit "${EXIT_FOLDER_CHANGED}"
  fi
  if ! err="$(folder_aws s3api head-object --bucket "${bucket}" --key "${prefix_path}${name}" \
      --if-match "${etag}" 2>&1 >/dev/null)"; then
    rm -f -- "${dest}"
    if [[ "${err}" == *"412"* || "${err}" == *"Precondition"* ]]; then
      echo "ERROR: ${name} in ${FASTQ_URL} changed while it was being copied" >&2
      exit "${EXIT_FOLDER_CHANGED}"
    fi
    echo "ERROR: couldn't check ${name} after copying it: $(head -c 300 <<<"${err}")" >&2
    exit "${EXIT_TRANSFER_FAILED}"
  fi
  copied=$((copied + 1))
done < <(FASTQ_FILES="${FASTQ_FILES}" stage-fastqs-lib rows)
echo "Copied ${copied} FASTQs from ${FASTQ_URL} (read ${read_as}), each checked against the recorded size and ETag"

prefix="$(fastq-sample-prefix "${DEST_DIR}")" || exit "${EXIT_BAD_FASTQ_NAMES}"
echo "FASTQ prefix: ${prefix}"
