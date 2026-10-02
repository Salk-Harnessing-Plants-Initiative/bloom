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
# The run's folder (RUN_DIR) is named by its run key, which a new run can reuse after a failed
# one. RUN_DIR/.inputs records what the folder was built from: a retry on the same inputs
# resumes, and anything else is cleared first. The old FASTQs are deleted, and the rest (its
# logs and Cell Ranger's errors) is moved to RUN_DIR.failed-<time>/. sra/ stays: fetch-sra
# writes it earlier in this same run.
#
# Env: SAMPLE, DEST_DIR, RUN_DIR (default: DEST_DIR's grandparent), and either FASTQ_URL with
#      FASTQ_FILES, or BUCKET. WAIT_SECONDS and POLL_SECONDS tune the wait.
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
BUCKET="${BUCKET:-bloomv2-workflows}"
WAIT_SECONDS="${WAIT_SECONDS:-600}"
POLL_SECONDS="${POLL_SECONDS:-30}"

if [ -z "${SAMPLE}" ] || [ -z "${DEST_DIR}" ]; then
  echo "ERROR: SAMPLE and DEST_DIR are required" >&2
  exit "${EXIT_BAD_INPUT}"
fi

# What this run's folder is built from, as one line.
if [ -n "${FASTQ_URL}" ]; then
  inputs="$(FASTQ_URL="${FASTQ_URL}" FASTQ_FILES="${FASTQ_FILES}" python3 -c '
import hashlib, os
print("folder", os.environ["FASTQ_URL"], hashlib.sha256(os.environ["FASTQ_FILES"].encode()).hexdigest())
')"
else
  inputs="raw_reads s3://${BUCKET}/raw_reads/${SAMPLE}/"
fi

# A folder left by a run on other inputs is set aside, so none of its FASTQs or results are
# taken for this run's.
mkdir -p "${RUN_DIR}"
if [ "$(cat "${RUN_DIR}/.inputs" 2>/dev/null)" != "${inputs}" ]; then
  shopt -s nullglob dotglob
  leftover=()
  for entry in "${RUN_DIR}"/*; do
    case "${entry##*/}" in sra | .inputs) ;; *) leftover+=("${entry}") ;; esac
  done
  shopt -u nullglob dotglob
  if [ "${#leftover[@]}" -gt 0 ]; then
    rm -rf -- "${RUN_DIR}/fastq"
    aside="${RUN_DIR}.failed-$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir -p "${aside}"
    for entry in "${leftover[@]}"; do
      [ -e "${entry}" ] && mv -- "${entry}" "${aside}/"
    done
    echo "Set aside an earlier run's folder (other inputs) in ${aside}; its FASTQs were deleted"
  fi
  printf '%s\n' "${inputs}" > "${RUN_DIR}/.inputs"
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

# The recorded FASTQs must be this run's sample, checked before anything is copied.
others="$(FASTQ_FILES="${FASTQ_FILES}" SAMPLE="${SAMPLE}" python3 -c '
import json, os, re
rule = re.compile(r"^(.+)_S[0-9]+_L[0-9]{3}_(R1|R2|I1|I2)_001\.fastq(\.gz)?$")
names = [f["name"] for f in json.loads(os.environ["FASTQ_FILES"])]
print(", ".join(sorted({m.group(1) for m in map(rule.match, names) if m and m.group(1) != os.environ["SAMPLE"]})))
')"
if [ -n "${others}" ]; then
  echo "ERROR: the FASTQs in ${FASTQ_URL} are named for ${others}, not the run's sample '${SAMPLE}'" >&2
  exit "${EXIT_OTHER_SAMPLE}"
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
  # Unsigned, as the start API checked it: the folder is public, and Bloom's own key reads no more.
  if ! listing="$(aws s3api list-objects-v2 --no-sign-request --bucket "${bucket}" --prefix "${prefix_path}" \
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

# Each file is copied, then checked: the copy has the recorded size, and the object still has
# the recorded ETag, so a file replaced while it was being copied isn't used.
copied=0
while IFS=$'\t' read -r name size etag; do
  dest="${DEST_DIR}/${name}"
  aws s3 cp --only-show-errors --no-sign-request "${FASTQ_URL}${name}" "${dest}" \
    || exit "${EXIT_TRANSFER_FAILED}"
  got="$(stat -c %s "${dest}")"
  if [ "${got}" != "${size}" ]; then
    rm -f -- "${dest}"
    echo "ERROR: ${name} copied as ${got} bytes, not the ${size} recorded; it changed while it was being copied" >&2
    exit "${EXIT_FOLDER_CHANGED}"
  fi
  if ! err="$(aws s3api head-object --no-sign-request --bucket "${bucket}" --key "${prefix_path}${name}" \
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
done < <(printf '%s' "${FASTQ_FILES}" | python3 -c '
import json, sys
for f in json.load(sys.stdin):
    print("%s\t%s\t%s" % (f["name"], f["size"], f["etag"]))
')
echo "Copied ${copied} FASTQs from ${FASTQ_URL}, each checked against the recorded size and ETag"

prefix="$(fastq-sample-prefix "${DEST_DIR}")" || exit "${EXIT_BAD_FASTQ_NAMES}"
echo "FASTQ prefix: ${prefix}"
