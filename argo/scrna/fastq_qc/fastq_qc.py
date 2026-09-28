#!/usr/bin/env python3
"""FASTQ QC for one 10x sample.

1. Count the reads and read lengths in every FASTQ.
2. Guess the 10x chemistry: the first 16 bases of R1 are the cell barcode, so check which
   10x barcode list they come from.
3. Optionally, compare the guess with the chemistry Cell Ranger picked.
"""

import argparse
import gzip
import json
import os
import re
import shlex
import subprocess
import sys

# From Cell Ranger 10.1.0 lib/python/cellranger/chemistry_defs.json: the barcode list each
# chemistry uses, and the R1 length it needs (16 bp barcode + UMI length).
WHITELISTS = [
    {"file": "737K-august-2016.txt", "chemistry": "SC3Pv2 or SC5P", "kit": "3' v2 or 5' v1/v2", "r1_needed": 26},
    {"file": "3M-february-2018_TRU.txt.gz", "chemistry": "SC3Pv3-polyA", "kit": "3' v3/v3.1", "r1_needed": 28},
    {"file": "3M-3pgex-may-2023_TRU.txt.gz", "chemistry": "SC3Pv4-polyA", "kit": "3' v4 (GEM-X)", "r1_needed": 28},
    {"file": "3M-5pgex-jan-2023.txt.gz", "chemistry": "SC5P-R2-v3", "kit": "5' v3 (GEM-X)", "r1_needed": 28},
    {"file": "737K-arc-v1.txt.gz", "chemistry": "ARC-v1", "kit": "Multiome GEX", "r1_needed": 28},
]
BARCODE_LENGTH = 16
MIN_SHARE_FOR_GUESS = 0.5

# <prefix>_S1_L001_R1_001.fastq.gz
FASTQ_NAME = re.compile(r"^(.+)_S\d+_L(\d{3})_(R1|R2|I1|I2)_001\.fastq\.gz$")


def find_fastqs(fastq_dir, s3_prefix):
    """Return (path, prefix, lane, read) for every Illumina-named FASTQ."""
    if fastq_dir:
        paths = [os.path.join(fastq_dir, name) for name in os.listdir(fastq_dir)]
    else:
        listing = subprocess.run(["aws", "s3", "ls", s3_prefix + "/"], check=True, capture_output=True, text=True)
        paths = [s3_prefix + "/" + line.split()[-1] for line in listing.stdout.splitlines()]
    found = []
    for path in sorted(paths):
        match = FASTQ_NAME.match(os.path.basename(path))
        if match:
            found.append((path, *match.groups()))
    return found


def read_stats(path, max_reads):
    """Count reads and lengths; stop after max_reads if given. Also return the R1 barcodes seen."""
    if path.startswith("s3://"):
        command = f"aws s3 cp {shlex.quote(path)} - | gzip -dc"
    else:
        command = f"gzip -dc {shlex.quote(path)}"
    proc = subprocess.Popen(["bash", "-o", "pipefail", "-c", command], stdout=subprocess.PIPE, text=True)

    reads, total_length, shortest, longest, barcodes = 0, 0, None, 0, []
    for line_number, line in enumerate(proc.stdout):
        if line_number % 4 != 1:  # the sequence is the 2nd of every 4 lines
            continue
        sequence = line.rstrip("\n")
        reads += 1
        total_length += len(sequence)
        shortest = len(sequence) if shortest is None else min(shortest, len(sequence))
        longest = max(longest, len(sequence))
        barcodes.append(sequence[:BARCODE_LENGTH])
        if max_reads and reads == max_reads:
            break

    if max_reads and reads == max_reads:
        proc.kill()
        proc.wait()
    elif proc.wait() != 0:
        raise RuntimeError(f"could not read {path}")
    stats = {"reads": reads, "min_len": shortest or 0, "max_len": longest,
             "mean_len": round(total_length / reads, 1) if reads else 0}
    return stats, barcodes


def guess_chemistry(barcodes, whitelist_dir):
    """Share of sampled barcodes found in each 10x list, best first; guess = best if share >= 0.5."""
    sampled = set(barcodes)
    scores = []
    for whitelist in WHITELISTS:
        path = os.path.join(whitelist_dir, whitelist["file"])
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt") as f:
            found = {line.strip() for line in f if line.strip() in sampled}
        share = sum(bc in found for bc in barcodes) / len(barcodes) if barcodes else 0
        scores.append({**whitelist, "share_of_barcodes": round(share, 3)})
    scores.sort(key=lambda s: s["share_of_barcodes"], reverse=True)
    guess = scores[0] if scores[0]["share_of_barcodes"] >= MIN_SHARE_FOR_GUESS else None
    return guess, scores


def cellranger_chemistry(detect_outs_path, guess):
    """Read the chemistry Cell Ranger picked and say whether it matches our guess."""
    with open(detect_outs_path) as f:
        gene_expression = json.load(f)["chemistry_defs"]["Gene Expression"]
    whitelist = gene_expression["barcode"][0]["whitelist"]["name"]
    guessed_whitelist = guess["file"].split(".txt")[0] if guess else None
    return {"name": gene_expression["name"], "whitelist": whitelist, "matches_guess": whitelist == guessed_whitelist}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--fastq-dir", help="local folder with the FASTQs")
    where.add_argument("--s3-prefix", help="s3://bloomv2-workflows/raw_reads/<sample>")
    parser.add_argument("--whitelist-dir", required=True, help="Cell Ranger's lib/python/cellranger/barcodes")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--sample-reads", type=int, default=200_000, help="R1 reads used to guess the chemistry")
    parser.add_argument("--quick", action="store_true", help="read only the first --sample-reads of each R1/R2 file")
    parser.add_argument("--cellranger-chemistry", help="Cell Ranger's DETECT_COUNT_CHEMISTRY/fork0/_outs")
    args = parser.parse_args()

    fastqs = find_fastqs(args.fastq_dir, args.s3_prefix.rstrip("/") if args.s3_prefix else None)
    problems = [] if fastqs else ["no FASTQs named <prefix>_S1_L001_R1_001.fastq.gz"]

    rows, barcodes = [], []
    for path, prefix, lane, read in fastqs:
        if args.quick and read not in ("R1", "R2"):
            continue
        print(f"reading {os.path.basename(path)}", flush=True)
        stats, file_barcodes = read_stats(path, args.sample_reads if args.quick else None)
        rows.append({"file": os.path.basename(path), "prefix": prefix, "lane": lane, "read": read, **stats})
        if read == "R1":
            barcodes += file_barcodes[: args.sample_reads - len(barcodes)]

    r1 = [r for r in rows if r["read"] == "R1"]
    r2 = [r for r in rows if r["read"] == "R2"]
    if not args.quick:
        for lane in sorted({r["lane"] for r in rows}):
            r1_reads = sum(r["reads"] for r in r1 if r["lane"] == lane)
            r2_reads = sum(r["reads"] for r in r2 if r["lane"] == lane)
            if r1_reads != r2_reads:
                problems.append(f"lane {lane}: R1 has {r1_reads} reads but R2 has {r2_reads}")

    guess, scores = guess_chemistry(barcodes, args.whitelist_dir)
    shortest_r1 = min((r["min_len"] for r in r1), default=0)
    if guess and shortest_r1 < guess["r1_needed"]:
        problems.append(f"shortest R1 is {shortest_r1} bp but {guess['kit']} needs {guess['r1_needed']} bp")
    if not guess:
        problems.append("could not guess the chemistry: no barcode list matched half the reads")

    detected = cellranger_chemistry(args.cellranger_chemistry, guess) if args.cellranger_chemistry else None
    prefixes = sorted({r["prefix"] for r in rows})
    summary = {
        "fastq_prefix": prefixes[0] if len(prefixes) == 1 else prefixes,
        "lanes": sorted({r["lane"] for r in rows}),
        "read_pairs": None if args.quick else sum(r["reads"] for r in r1),
        "r1_length": {"min": shortest_r1, "max": max((r["max_len"] for r in r1), default=0)},
        "r2_length": {"min": min((r["min_len"] for r in r2), default=0), "max": max((r["max_len"] for r in r2), default=0)},
        "chemistry_guess": guess,
        "whitelist_scores": scores,
        "cellranger_chemistry": detected,
        "problems": problems,
        "passed": not problems,
    }

    os.makedirs(args.out_dir, exist_ok=True)
    columns = ["file", "prefix", "lane", "read", "reads", "min_len", "mean_len", "max_len"]
    with open(os.path.join(args.out_dir, "fastq_stats.tsv"), "w") as f:
        f.write("\t".join(columns) + "\n")
        f.writelines("\t".join(str(r[c]) for c in columns) + "\n" for r in rows)
    with open(os.path.join(args.out_dir, "qc_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    print("read pairs:", summary["read_pairs"], "| R1:", summary["r1_length"], "| R2:", summary["r2_length"])
    print("chemistry guess:", f"{guess['kit']} ({guess['share_of_barcodes']:.0%} of barcodes)" if guess else "none")
    if detected:
        print("cellranger picked:", detected["name"], "- matches guess" if detected["matches_guess"] else "- DOES NOT match guess")
    for problem in problems:
        print("PROBLEM:", problem)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
