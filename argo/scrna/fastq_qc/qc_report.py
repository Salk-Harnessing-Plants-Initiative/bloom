#!/usr/bin/env python3
"""Write README.md for one sample's QC folder: read counts, chemistry guess and FastQC verdicts, explained."""

import glob
import json
import os
import sys
import zipfile

# FastQC checks that commonly WARN or FAIL on 10x data without meaning a problem.
EXPECTED = {
    "R1": {
        "Per base sequence content": "R1 is cell barcode + UMI, not biology, so base composition is not random.",
        "Per sequence GC content": "barcodes and UMIs do not follow a genome GC distribution.",
        "Sequence Duplication Levels": "many reads share a barcode.",
        "Overrepresented sequences": "barcodes from large cells appear many times.",
    },
    "R2": {
        "Sequence Duplication Levels": "highly expressed genes give many identical reads; normal for single-cell.",
        "Per sequence GC content": "a few very highly expressed genes skew the GC curve.",
        "Overrepresented sequences": "usually mitochondrial, ribosomal or other very highly expressed transcripts.",
    },
}


def fastqc_verdicts(qc_dir):
    """Map FastQC report name -> list of (verdict, check) from each zip's summary.txt."""
    verdicts = {}
    for path in sorted(glob.glob(os.path.join(qc_dir, "fastqc", "*_fastqc.zip"))):
        name = os.path.basename(path)[: -len("_fastqc.zip")]
        with zipfile.ZipFile(path) as z:
            summary = z.read(f"{name}_fastqc/summary.txt").decode()
        verdicts[name] = [tuple(line.split("\t")[:2]) for line in summary.splitlines() if line]
    return verdicts


def main(qc_dir):
    with open(os.path.join(qc_dir, "qc_summary.json")) as f:
        s = json.load(f)
    guess = s["chemistry_guess"]
    lines = [
        f"# QC: {s['fastq_prefix']}",
        "",
        f"**Result: {'PASSED' if s['passed'] else 'PROBLEMS FOUND'}**",
        "",
        "## Reads",
        "",
        f"- Read pairs: {s['read_pairs']:,}" if s["read_pairs"] is not None else "- Read pairs: not counted",
        f"- Lanes: {', '.join(s['lanes'])}",
        f"- R1 length: {s['r1_length']['min']}-{s['r1_length']['max']} bp (barcode + UMI)",
        f"- R2 length: {s['r2_length']['min']}-{s['r2_length']['max']} bp (cDNA)",
        f"- FASTQ prefix for Cell Ranger `--sample`: `{s['fastq_prefix']}`",
        "",
        "## Chemistry guess",
        "",
    ]
    if guess:
        lines.append(f"**{guess['kit']}** (`{guess['chemistry']}`): {guess['share_of_barcodes']:.0%} of sampled R1 "
                     f"barcodes are on the `{guess['file']}` list. Needs R1 of at least {guess['r1_needed']} bp.")
    else:
        lines.append("No guess: no 10x barcode list matched half of the sampled R1 barcodes.")
    lines += ["", "| Barcode list | Kit | Share of barcodes |", "|---|---|---|"]
    lines += [f"| `{w['file']}` | {w['kit']} | {w['share_of_barcodes']:.1%} |" for w in s["whitelist_scores"]]
    lines += ["", "Cell Ranger runs with `--chemistry auto`; its choice is compared with this guess after the count.", ""]

    if s["problems"]:
        lines += ["## Problems", ""] + [f"- {p}" for p in s["problems"]] + [""]

    lines += ["## FastQC", "", "Judge quality on R2. R1 is barcode + UMI, so several checks flag it on every 10x run.", ""]
    for name, checks in fastqc_verdicts(qc_dir).items():
        read = "R1" if "_R1_" in name else "R2"
        flagged = [(v, c) for v, c in checks if v != "PASS"]
        lines.append(f"### {name}")
        lines.append("")
        if not flagged:
            lines.append("All checks passed.")
        for verdict, check in flagged:
            note = EXPECTED[read].get(check)
            lines.append(f"- {verdict} {check}: " + (f"expected, {note}" if note else "**not typical for 10x; open the HTML report**"))
        lines.append(f"- Full report: `fastqc/{name}_fastqc.html`")
        lines.append("")

    with open(os.path.join(qc_dir, "README.md"), "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1])
