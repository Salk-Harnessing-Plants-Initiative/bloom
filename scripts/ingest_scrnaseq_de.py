#!/usr/bin/env python3
"""Write a single-cell dataset's differential expression into the explorer.

Reads the two files the analysis produces -- the summary of every comparison it
considered, and the per-gene results for the ones that ran -- and records them as
one analysis: a run, a row per comparison, and a row per gene tested.

A skipped comparison still gets a row, marked untested, so the panel can say
"not tested" rather than leaving a cell type silently absent.

The summary's counts are not stored, but they are checked: each is recomputed
from the results, and the load is refused if they disagree.

The cells have to be loaded first, by scripts/ingest_scrnaseq.py, and the genes
registered, by scripts/ingest_scrnaseq_counts.py. It signs in to the site as a
writer (or admin) account and writes through the API. The password is read from
BLOOM_PASSWORD, never from the command line:

    BLOOM_PASSWORD=... uv run --with supabase \
      python scripts/ingest_scrnaseq_de.py \
        --server https://staging.bloom.salk.edu --email you@salk.edu \
        --results ALL_LEVEL1_DE_RESULTS.tsv --summary LEVEL1_DE_SUMMARY.tsv \
        --dataset-name "MYB41 transgene" --species-id 1 --method seurat-wilcoxon

--api-url and --anon-key give the API address instead of --server. How an admin
makes an account a writer is in scripts/ingest_scrnaseq.py.

The two files are one analysis, identified by their checksums. If a load stops,
run the same command again: it adds only the comparisons and gene rows still
missing, and until then the analysis shows with gene rows missing. Different
files make a new analysis beside the old one. Load a dataset from one terminal at
a time.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrna_ingest_api as ingest_api

IngestError = ingest_api.IngestError

# The results name genes with the annotation release appended; the counts loader
# strips it when registering genes, so stripping it here finds them.
GENE_SUFFIX = ingest_api.RELEASE_SUFFIX

# Float noise at the bounds of a fraction: the real export writes 1.0000000000000002.
FRACTION_MARGIN = 1e-9

# The export compares genotypes within a cell type.
GROUP_KIND = "genotype"

# Gene rows per insert request. An analysis is roughly 675k rows.
GENE_BATCH = 10_000

# Fields the two files must carry, so a changed export is refused by name.
SUMMARY_FIELDS = ("celltype", "contrast", "group1", "group2", "n_group1",
                  "n_group2", "tested", "n_genes_tested", "n_FDR_0.05",
                  "n_FDR_0.05_abs_log2FC_0.5", "n_up", "n_down")
RESULT_FIELDS = ("celltype", "contrast", "gene", "log2FC", "pvalue", "FDR",
                 "pct_expr_group1", "pct_expr_group2", "sig_FDR_0.05",
                 "sig_FDR_0.05_abs_log2FC_0.5")

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results", type=Path, required=True,
                   help="ALL_LEVEL1_DE_RESULTS.tsv — every gene of every "
                        "comparison that ran")
    p.add_argument("--summary", type=Path, required=True,
                   help="LEVEL1_DE_SUMMARY.tsv — one row per comparison "
                        "considered, tested or not")
    p.add_argument("--dataset-name", required=True)
    p.add_argument("--species-id", type=int, required=True)
    p.add_argument("--method", required=True,
                   help="the test that produced the results, e.g. "
                        "seurat-wilcoxon; recorded on the analysis")
    p.add_argument("--notes", type=Path,
                   help="a JSON object of notes recorded with the analysis, such as "
                        "how its cells were chosen; written when it is first recorded")
    p.add_argument("--server", help="the site, e.g. https://staging.bloom.salk.edu; "
                   "the API address is read from it")
    p.add_argument("--api-url", help="the API address, instead of reading it from --server")
    p.add_argument("--anon-key", help="the site's public key, with --api-url")
    p.add_argument("--email", help="the writer account to sign in as; the password "
                   "is read from BLOOM_PASSWORD")
    p.add_argument("--dry-run", action="store_true",
                   help="read and check everything, write nothing")
    return p.parse_args(argv)


def _rows(path: Path, required: tuple[str, ...]) -> list[dict]:
    if not path.exists():
        raise IngestError(f"no such file: {path}")
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise IngestError(
                f"{path.name} has no column {', '.join(missing)}; found "
                f"{', '.join(reader.fieldnames or ['nothing'])}"
            )
        rows = list(reader)
    if not rows:
        raise IngestError(f"{path.name} holds no rows")
    return rows


def _flag(value: str) -> bool:
    """The exports write Python booleans; anything else is refused, not guessed."""
    if value not in ("True", "False"):
        raise IngestError(f"expected True or False, got {value!r}")
    return value == "True"


def _where(row: dict) -> str:
    """Enough of a results row to find it in a file of hundreds of thousands."""
    return f"{row['celltype']} / {row['contrast']}, gene {row['gene']}"


def _number(row: dict, column: str) -> float:
    """A finite number from one results column, or a refusal naming the gene."""
    value = row[column]
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise IngestError(
            f"{_where(row)}: {column} is {value!r}, which is not a number"
        ) from None
    if not math.isfinite(number):
        raise IngestError(f"{_where(row)}: {column} is {value!r}, not a finite number")
    return number


def _fraction(row: dict, column: str) -> float:
    """A value between 0 and 1: a probability or a proportion of cells."""
    number = _number(row, column)
    if -FRACTION_MARGIN <= number < 0 or 1 < number <= 1 + FRACTION_MARGIN:
        return float(round(number))
    if not 0 <= number <= 1:
        hint = "; it looks like a percentage" if 1 < number <= 100 else ""
        raise IngestError(
            f"{_where(row)}: {column} is {number}, outside 0 to 1{hint}"
        )
    return number


def _fold_change(row: dict) -> float | None:
    """log2FC, or None where the analysis could not compute one.

    R writes NA or NaN when both groups express nothing. That is stored as no
    fold change. An infinite one is kept: it is a measurement, not a gap.
    """
    if row["log2FC"] == "NA":
        return None
    try:
        number = float(row["log2FC"])
    except (TypeError, ValueError):
        raise IngestError(
            f"{_where(row)}: log2FC is {row['log2FC']!r}, which is not a number"
        ) from None
    return None if math.isnan(number) else number


def read_summary(path: Path) -> list[dict]:
    """One row per comparison the analysis considered."""
    out = []
    for row in _rows(path, SUMMARY_FIELDS):
        tested = _flag(row["tested"])
        entry = {
            "celltype": row["celltype"],
            "contrast": row["contrast"],
            "group1": row["group1"],
            "group2": row["group2"],
            "n_group1": int(row["n_group1"]),
            "n_group2": int(row["n_group2"]),
            "tested": tested,
            "counts": {
                "n_genes_tested": int(row["n_genes_tested"]),
                "n_significant_fdr": int(row["n_FDR_0.05"]),
                "n_significant_fdr_lfc": int(row["n_FDR_0.05_abs_log2FC_0.5"]),
                "n_up": int(float(row["n_up"])),
                "n_down": int(float(row["n_down"])),
            } if tested else None,
        }
        if entry["group1"] == entry["group2"]:
            raise IngestError(
                f"{entry['celltype']} / {entry['contrast']} compares "
                f"{entry['group1']} against itself"
            )
        out.append(entry)

    seen = {(e["celltype"], e["contrast"]) for e in out}
    if len(seen) != len(out):
        raise IngestError(
            f"{path.name} names the same comparison twice; there must be one "
            f"row per cell type and contrast"
        )
    return out


def read_results(path: Path) -> dict:
    """The per-gene rows, grouped by the comparison they belong to."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    suffixed = 0
    for row in _rows(path, RESULT_FIELDS):
        gene = GENE_SUFFIX.sub("", row["gene"])
        if gene != row["gene"]:
            suffixed += 1
        pvalue, fdr = _fraction(row, "pvalue"), _fraction(row, "FDR")
        if fdr < pvalue:
            raise IngestError(
                f"{_where(row)}: FDR {fdr} is below its own p-value {pvalue}"
            )
        grouped[(row["celltype"], row["contrast"])].append({
            "gene": gene,
            "log2fc": _fold_change(row),
            "pvalue": pvalue,
            "fdr": fdr,
            "pct_1": _fraction(row, "pct_expr_group1"),
            "pct_2": _fraction(row, "pct_expr_group2"),
            # Not written; the summary's counts are checked against them.
            "_fdr": _flag(row["sig_FDR_0.05"]),
            "_fdr_lfc": _flag(row["sig_FDR_0.05_abs_log2FC_0.5"]),
        })

    for key, rows in grouped.items():
        names = [r["gene"] for r in rows]
        if len(set(names)) != len(names):
            raise IngestError(
                f"{key[0]} / {key[1]} names the same gene twice once the "
                f"annotation release is stripped from it"
            )
    return {"groups": dict(grouped), "suffixed": suffixed}


def recount(rows: list[dict]) -> dict[str, int]:
    """What the summary should say, worked out from the rows themselves."""
    significant = [r for r in rows if r["_fdr_lfc"]]
    up = sum(1 for r in significant if r["log2fc"] is not None and r["log2fc"] > 0)
    return {
        "n_genes_tested": len(rows),
        "n_significant_fdr": sum(1 for r in rows if r["_fdr"]),
        "n_significant_fdr_lfc": len(significant),
        "n_up": up,
        "n_down": len(significant) - up,
    }


def reconcile(summary: list[dict], groups: dict[tuple[str, str], list[dict]]) -> None:
    """Refuse any summary row the results file does not bear out."""
    for entry in summary:
        key = (entry["celltype"], entry["contrast"])
        rows = groups.get(key)
        if not entry["tested"]:
            if rows:
                raise IngestError(
                    f"{key[0]} / {key[1]} is marked untested but the results "
                    f"hold {len(rows)} genes for it"
                )
            continue
        if not rows:
            raise IngestError(
                f"{key[0]} / {key[1]} is marked tested but the results hold no "
                f"genes for it"
            )
        counted = recount(rows)
        for field, expected in entry["counts"].items():
            if counted[field] != expected:
                raise IngestError(
                    f"{key[0]} / {key[1]}: the summary says {field} is "
                    f"{expected}, the results give {counted[field]}"
                )

    extra = set(groups) - {(e["celltype"], e["contrast"]) for e in summary}
    if extra:
        first = sorted(extra)[:3]
        raise IngestError(
            f"{len(extra)} comparisons appear in the results with no summary "
            f"row: {', '.join(f'{c} / {k}' for c, k in first)}"
        )


def read_notes(path: Path | None) -> dict | None:
    """Notes recorded with the analysis; they do not change its fingerprint."""
    if path is None:
        return None
    if not path.exists():
        raise IngestError(f"no such file: {path}")
    try:
        notes = json.loads(path.read_text())
    except ValueError as exc:
        raise IngestError(f"{path.name} is not JSON: {exc}") from None
    if not isinstance(notes, dict):
        raise IngestError(f"{path.name} must hold a JSON object of notes")
    return notes


def fingerprint(summary: Path, results: Path) -> tuple[dict, str]:
    """What this analysis was loaded from, and a hash that identifies it.

    Two loads with the same hash are the same files, so the second is refused
    rather than recorded as a second analysis that agrees with the first.
    """
    params = {
        "summary_sha256": hashlib.sha256(summary.read_bytes()).hexdigest(),
        "results_sha256": hashlib.sha256(results.read_bytes()).hexdigest(),
    }
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return params, hashlib.sha256(canonical.encode()).hexdigest()


def open_dataset(writer, name: str, species_id: int) -> int:
    found = ingest_api.find_dataset(writer, species_id, name)
    if found is None:
        raise IngestError(
            f"no dataset named {name.strip()!r} for species {species_id}. Load its "
            f"cells first with scripts/ingest_scrnaseq.py"
        )
    if not found.get("ingested_at"):
        raise IngestError(
            f"dataset {found['id']}'s cells are not finished. Finish them first by "
            f"running scripts/ingest_scrnaseq.py again"
        )
    return found["id"]


def check_cell_types(writer, dataset_id: int, summary: list[dict]) -> None:
    """Every cell type named here has to exist in the dataset's catalogue.

    The database refuses it too; this says which ones and what to do about it.
    """
    catalogue = {r["cluster_id"] for r in ingest_api.read_all(
        writer, "scrna_clusters", "cluster_id", filters=[("eq", "dataset_id", dataset_id)])}
    if not catalogue:
        raise IngestError(
            f"dataset {dataset_id} has no cell types. Load its cells first with "
            f"scripts/ingest_scrnaseq.py"
        )
    unknown = sorted({e["celltype"] for e in summary} - catalogue)
    if unknown:
        raise IngestError(
            f"{len(unknown)} cell types are not in the catalogue: "
            f"{', '.join(unknown[:5])}"
        )


def gene_ids(writer, dataset_id: int,
             groups: dict[tuple[str, str], list[dict]]) -> dict[str, int]:
    """The catalogue id of every gene the results name.

    Gene rows reference the catalogue, so the genes have to be registered first,
    and a name registered twice cannot be resolved to one gene.
    """
    registered = ingest_api.read_all(writer, "scrna_genes", "id,gene_name",
                                     filters=[("eq", "dataset_id", dataset_id)])
    if not registered:
        raise IngestError(
            f"dataset {dataset_id} has no registered genes. Load its gene "
            f"counts first with scripts/ingest_scrnaseq_counts.py"
        )
    ids: dict[str, int] = {}
    twice = set()
    for row in registered:
        if row["gene_name"] in ids:
            twice.add(row["gene_name"])
        ids[row["gene_name"]] = row["id"]

    named = {r["gene"] for rows in groups.values() for r in rows}
    ambiguous = sorted(named & twice)
    if ambiguous:
        raise IngestError(
            f"{len(ambiguous)} genes are registered more than once for this "
            f"dataset, so a result cannot say which it means: "
            f"{', '.join(ambiguous[:5])}"
        )
    missing = sorted(named - set(ids))
    if missing:
        raise IngestError(
            f"{len(missing)} genes in the results are not registered for this "
            f"dataset: {', '.join(missing[:5])}"
        )
    return ids


def find_run(writer, dataset_id: int, method: str, params_hash: str) -> dict | None:
    """The batch analysis already recorded from these two files, if any."""
    runs = ingest_api.read_all(writer, "scrna_de_runs", "id,method", filters=[
        ("eq", "dataset_id", dataset_id), ("eq", "params_hash", params_hash),
        ("eq", "source", "batch")])
    if len(runs) > 1:
        raise IngestError(
            f"these two files are recorded for dataset {dataset_id} as "
            f"{len(runs)} analyses ({', '.join(str(r['id']) for r in runs)}); cannot "
            f"tell which to continue. An admin has to remove the extra ones"
        )
    if runs and runs[0]["method"] != method:
        raise IngestError(
            f"these two files are recorded for dataset {dataset_id} as analysis "
            f"{runs[0]['id']} with method {runs[0]['method']!r}; this load says "
            f"{method!r}"
        )
    return runs[0] if runs else None


def plan(summary: list[dict], groups: dict[tuple[str, str], list[dict]],
         existing: dict[tuple[str, str], int], have: dict[int, set[int]],
         ids: dict[str, int]) -> tuple[list[dict], dict[tuple[str, str], list[dict]]]:
    """What is missing: the comparisons not yet written, and for each comparison the
    gene rows it does not hold yet."""
    new = [e for e in summary if (e["celltype"], e["contrast"]) not in existing]
    genes = {}
    for e in summary:
        key = (e["celltype"], e["contrast"])
        held = have.get(existing.get(key), set())
        missing = [r for r in groups.get(key, []) if ids[r["gene"]] not in held]
        if missing:
            genes[key] = missing
    return new, genes


def _json_number(value: float | None):
    """JSON has no spelling for infinity; Postgres reads this text as the number."""
    if value is not None and math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    return value


def comparison_row(dataset_id: int, run_id: int, method: str, params_hash: str,
                   e: dict, n_genes: int) -> dict:
    return {"dataset_id": dataset_id, "run_id": run_id, "cluster_id": e["celltype"],
            "contrast": e["contrast"], "group1": e["group1"], "group2": e["group2"],
            "n_group1": e["n_group1"], "n_group2": e["n_group2"],
            "group_kind": GROUP_KIND, "method": method, "params_hash": params_hash,
            "tested": e["tested"], "n_genes_tested": n_genes}


def gene_row(dataset_id: int, de_id: int, gene_id: int, r: dict) -> dict:
    return {"de_id": de_id, "dataset_id": dataset_id, "gene_id": gene_id,
            "log2fc": _json_number(r["log2fc"]), "pvalue": r["pvalue"], "fdr": r["fdr"],
            "pct_1": r["pct_1"], "pct_2": r["pct_2"]}


def _stored(writer, run_id: int) -> tuple[dict, dict]:
    existing = {(r["cluster_id"], r["contrast"]): r["id"] for r in ingest_api.read_all(
        writer, "scrna_de", "id,cluster_id,contrast", filters=[("eq", "run_id", run_id)])}
    have: dict[int, set[int]] = defaultdict(set)
    if existing:
        for r in ingest_api.read_all(writer, "scrna_de_genes", "id,de_id,gene_id",
                                     filters=[("in_", "de_id", sorted(existing.values()))]):
            have[r["de_id"]].add(r["gene_id"])
    return existing, have


def load(writer, name: str, species_id: int, method: str, params: dict,
         params_hash: str, summary: list[dict],
         groups: dict[tuple[str, str], list[dict]]) -> tuple[int, int, int, str]:
    """Record the analysis, or continue the one these files already started.

    Returns the run id, the comparisons and gene rows written, and "loaded",
    "resumed" or "already loaded".
    """
    dataset_id = open_dataset(writer, name, species_id)
    check_cell_types(writer, dataset_id, summary)
    ids = gene_ids(writer, dataset_id, groups)

    run = find_run(writer, dataset_id, method, params_hash)
    if run is None:
        (run,) = ingest_api.insert(writer, "record the analysis", "scrna_de_runs", [{
            "dataset_id": dataset_id, "source": "batch", "status": "complete",
            "method": method, "params": params, "params_hash": params_hash,
            "requested_by": writer.session.user_id,
            "completed_at": datetime.now(UTC).isoformat()}], returning=True)
        existing, have, outcome = {}, {}, "loaded"
    else:
        (existing, have), outcome = _stored(writer, run["id"]), "resumed"

    new, missing = plan(summary, groups, existing, have, ids)
    if outcome == "resumed" and not new and not missing:
        return run["id"], 0, 0, "already loaded"
    if new:
        written = ingest_api.insert(
            writer, f"write {len(new)} comparisons", "scrna_de",
            [comparison_row(dataset_id, run["id"], method, params_hash, e,
                            len(groups.get((e["celltype"], e["contrast"]), [])))
             for e in new], returning=True)
        existing = {**existing, **{(r["cluster_id"], r["contrast"]): r["id"]
                                   for r in written}}
    genes = 0
    for key, rows in missing.items():
        for start in range(0, len(rows), GENE_BATCH):
            batch = rows[start:start + GENE_BATCH]
            ingest_api.insert(writer, f"write gene rows of {key[0]} / {key[1]}",
                              "scrna_de_genes", [gene_row(dataset_id, existing[key],
                                                          ids[r["gene"]], r) for r in batch])
            genes += len(batch)
    return run["id"], len(new), genes, outcome


def describe(summary: list[dict], read: dict) -> str:
    tested = sum(1 for e in summary if e["tested"])
    genes = sum(len(rows) for rows in read["groups"].values())
    no_fold = sum(1 for rows in read["groups"].values() for r in rows
                  if r["log2fc"] is None)
    return (
        f"{len(summary)} comparisons: {tested} tested, {len(summary) - tested} "
        f"skipped\n"
        f"  every count in the summary agrees with the results\n"
        f"  {genes} gene results, {no_fold} with no fold change\n"
        f"  annotation release stripped from {read['suffixed']} gene names"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        summary = read_summary(args.summary)
        read = read_results(args.results)
        reconcile(summary, read["groups"])
        params, params_hash = fingerprint(args.summary, args.results)
        notes = read_notes(args.notes)
    except IngestError as exc:
        print(f"refusing to ingest: {exc}", file=sys.stderr)
        return 1

    print(describe(summary, read))
    if notes:
        params = {**params, "notes": notes}
        print(f"  notes recorded with the analysis: {', '.join(sorted(notes))}")
    if args.dry_run:
        genes = sum(len(rows) for rows in read["groups"].values())
        print(f"  would write one analysis of {len(summary)} comparisons and {genes} "
              f"gene rows")
        print("dry run — nothing written")
        return 0

    if not args.email:
        print("refusing to ingest: --email names the account to sign in as; its "
              "password is read from BLOOM_PASSWORD", file=sys.stderr)
        return 1

    name = args.dataset_name.strip()
    marker = ingest_api.Marker(ingest_api.marker_path(args.results, name))
    try:
        marker.check()
        password = ingest_api.read_password()
        api_url, anon_key = ingest_api.resolve_api(args.server, args.api_url, args.anon_key)
        session = ingest_api.sign_in(api_url, anon_key, args.email, password)
        run_id, comparisons, genes, outcome = load(
            ingest_api.Writer(session, marker), args.dataset_name, args.species_id,
            args.method, params, params_hash, summary, read["groups"])
    except IngestError as exc:
        print(f"refusing to ingest: {exc}", file=sys.stderr)
        return 1

    if outcome == "already loaded":
        print(f"  these two files are already loaded for dataset {name} as analysis "
              f"{run_id}")
    else:
        verb = "wrote" if outcome == "loaded" else "resumed"
        print(f"  {verb} analysis {run_id}: {comparisons} comparisons and {genes} "
              f"gene rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
