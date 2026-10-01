#!/usr/bin/env bash
# Copy one run's FASTQs onto the run's folder on the shared disk.
#
# With FASTQ_URL set, the reads come from that S3 folder: exactly the files listed in
# FASTQ_FILES (a JSON list of {name, size, etag}, recorded when the run was started), and
# nothing is written to S3. If the folder holds no FASTQ yet, it waits up to WAIT_SECONDS in
# case an upload is still finishing. A file added, removed, resized or replaced since the
# run was started fails the step, so the run never uses other reads than it was started on.
# Without FASTQ_URL, the reads come from s3://<bucket>/raw_reads/<sample>/ as before.
#
# Env: SAMPLE, DEST_DIR, and either FASTQ_URL with FASTQ_FILES, or BUCKET. WAIT_SECONDS and
#      POLL_SECONDS tune the wait.
# Exit codes: 0 copied, 4 no FASTQs, 6 bad input, 7 misnamed FASTQs (fastq-sample-prefix),
#      8 the folder changed since the run was started, 10 S3 couldn't be listed or read.
set -euo pipefail

readonly EXIT_NO_FASTQS=4
readonly EXIT_BAD_INPUT=6
readonly EXIT_BAD_FASTQ_NAMES=7
readonly EXIT_FOLDER_CHANGED=8
readonly EXIT_TRANSFER_FAILED=10
readonly URL_RULE='^s3://([a-z0-9][a-z0-9.-]{1,61}[a-z0-9])/(([A-Za-z0-9!_.*'"'"'()-]+/)+)$'

SAMPLE="${SAMPLE:-}"
DEST_DIR="${DEST_DIR:-}"
FASTQ_URL="${FASTQ_URL:-}"
FASTQ_FILES="${FASTQ_FILES:-}"
BUCKET="${BUCKET:-bloomv2-workflows}"
WAIT_SECONDS="${WAIT_SECONDS:-600}"
POLL_SECONDS="${POLL_SECONDS:-30}"

if [ -z "${SAMPLE}" ] || [ -z "${DEST_DIR}" ]; then
  echo "ERROR: SAMPLE and DEST_DIR are required" >&2
  exit "${EXIT_BAD_INPUT}"
fi
mkdir -p "${DEST_DIR}"

if [ -z "${FASTQ_URL}" ]; then
  src="s3://${BUCKET}/raw_reads/${SAMPLE}/"
  aws s3 sync --only-show-errors "${src}" "${DEST_DIR}/" || exit "${EXIT_TRANSFER_FAILED}"
  # sync from a missing prefix copies nothing and still exits 0
  if ! compgen -G "${DEST_DIR}/*.fastq*" >/dev/null; then
    echo "ERROR: no FASTQs at ${src}" >&2
    exit "${EXIT_NO_FASTQS}"
  fi
  prefix="$(fastq-sample-prefix "${DEST_DIR}")" || exit "${EXIT_BAD_FASTQ_NAMES}"
  echo "FASTQ prefix: ${prefix}"
  exit 0
fi

bucket=""
if [[ "${FASTQ_URL}" =~ ${URL_RULE} ]]; then
  bucket="${BASH_REMATCH[1]}"
  prefix_path="${BASH_REMATCH[2]}"
fi
if [ -z "${bucket}" ] || [[ "${FASTQ_URL}" =~ /\.{1,2}/ ]]; then
  echo "ERROR: '${FASTQ_URL}' is not a folder like s3://bucket/folder/" >&2
  exit "${EXIT_BAD_INPUT}"
fi
if ! printf '%s' "${FASTQ_FILES}" | python3 -c 'import json, sys; f = json.load(sys.stdin); assert isinstance(f, list) and f' 2>/dev/null; then
  echo "ERROR: FASTQ_FILES must be the run's list of files" >&2
  exit "${EXIT_BAD_INPUT}"
fi

# Compares LISTING (the folder's list-objects-v2 JSON) with FASTQ_FILES. Prints "match",
# "empty" (no FASTQ yet), "partial: <missing>" (only some files, all unchanged) or
# "changed: <reason>".
compare() {
  python3 -c '
import json, os
expected = {f["name"]: f for f in json.loads(os.environ["FASTQ_FILES"])}
prefix = os.environ["PREFIX_PATH"]
listing = json.loads(os.environ.get("LISTING") or "{}") or {}
found = {}
for obj in listing.get("Contents", []):
    name = obj["Key"][len(prefix):]
    if name.endswith((".fastq", ".fastq.gz")) and "/" not in name:
        found[name] = obj
if not found:
    print("empty")
    raise SystemExit
for name, obj in sorted(found.items()):
    want = expected.get(name)
    if want is None:
        print("changed: %s was added" % name)
    elif obj["Size"] != want["size"]:
        print("changed: %s is %s bytes, not %s" % (name, obj["Size"], want["size"]))
    elif obj["ETag"] != want["etag"]:
        print("changed: %s was replaced (its ETag differs)" % name)
    else:
        continue
    raise SystemExit
missing = sorted(set(expected) - set(found))
print("partial: " + ", ".join(missing) if missing else "match")
'
}

deadline=$(( $(date +%s) + WAIT_SECONDS ))
while :; do
  if ! listing="$(aws s3api list-objects-v2 --bucket "${bucket}" --prefix "${prefix_path}" \
      --delimiter / --max-keys 1000 --no-paginate --output json 2>/tmp/list.err)"; then
    echo "ERROR: couldn't list ${FASTQ_URL}: $(head -c 300 /tmp/list.err)" >&2
    exit "${EXIT_TRANSFER_FAILED}"
  fi
  state="$(LISTING="${listing}" PREFIX_PATH="${prefix_path}" FASTQ_FILES="${FASTQ_FILES}" compare)"
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

mapfile -t names < <(printf '%s' "${FASTQ_FILES}" | python3 -c 'import json, sys; [print(f["name"]) for f in json.load(sys.stdin)]')
for name in "${names[@]}"; do
  aws s3 cp --only-show-errors "${FASTQ_URL}${name}" "${DEST_DIR}/${name}" || exit "${EXIT_TRANSFER_FAILED}"
done
echo "Copied ${#names[@]} FASTQs from ${FASTQ_URL}"

prefix="$(fastq-sample-prefix "${DEST_DIR}")" || exit "${EXIT_BAD_FASTQ_NAMES}"
if [ "${prefix}" != "${SAMPLE}" ]; then
  echo "ERROR: the FASTQs are for '${prefix}', not the run's sample '${SAMPLE}'" >&2
  exit "${EXIT_FOLDER_CHANGED}"
fi
echo "FASTQ prefix: ${prefix}"
