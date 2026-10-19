"""go-pathways <step>: the GO and pathway workflow steps.

fetch-references downloads the GO ontology and each source's own files (KEGG's pathway list for
the species, TAIR's GO annotations); the annotation file records the releases. fetch-de reads a dataset's genes and one complete differential expression analysis from Bloom
(signed in as bloomctl does, ~/.bloom) into a JSON file. build-annotation turns eggNOG-mapper's
output, or TAIR's GO annotations, into a genome version's annotation file. enrich tests the
fetched analysis against an annotation file and writes the enrichment file. upload-annotation
and upload-enrichment record a file in Bloom (catalogue.py); fetch-annotation fetches a ready
build's file back.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from . import annotation, catalogue, enrichment, go_terms
from .steps import EXIT_BAD_ANNOTATION, EXIT_BAD_ENRICHMENT_INPUT, EXIT_NO_CREDENTIALS, StepError

SOURCES = ("eggnog", "tair-go")
# PostgREST returns at most this many rows per request.
PAGE = 1000
GO_URL = "https://current.geneontology.org/ontology/go-basic.obo"
TAIR_URL = "https://current.geneontology.org/annotations/tair.gaf.gz"
KEGG_URL = "https://rest.kegg.jp/list/pathway/{org}"
KEGG_ORG = re.compile(r"^[a-z]{3,4}$")
# How long one download may take, in seconds.
DOWNLOAD_TIMEOUT = 600
# The Gene Ontology's servers refuse Python's default user agent with 403.
USER_AGENT = "bloom-go-pathways/1 (+https://github.com/Salk-Harnessing-Plants-Initiative/bloom)"


# --------------------------------------------------------------------------- #
# fetch-references
# --------------------------------------------------------------------------- #


def reference_urls(source: str, kegg_org: str) -> dict[str, str]:
    """The files a source's annotation is built from, by the name they are saved under."""
    urls = {"go-basic.obo": GO_URL}
    if source == "tair-go":
        urls["tair.gaf.gz"] = TAIR_URL
    elif kegg_org:
        if not KEGG_ORG.match(kegg_org):
            raise StepError(EXIT_BAD_ANNOTATION,
                            f"{kegg_org!r} is not a KEGG organism code (3-4 lowercase letters)")
        urls["kegg-pathways.tsv"] = KEGG_URL.format(org=kegg_org)
    return urls


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def _download(url: str, path: Path) -> None:
    with urllib.request.urlopen(_request(url), timeout=DOWNLOAD_TIMEOUT) as response:
        path.write_bytes(response.read())


# --------------------------------------------------------------------------- #
# fetch-de
# --------------------------------------------------------------------------- #


def pick_de_run(runs: list[dict], wanted: int | None) -> int:
    """The named analysis if complete, else the dataset's newest complete one."""
    if wanted is not None:
        run = next((r for r in runs if r["id"] == wanted), None)
        if run is None:
            raise StepError(EXIT_BAD_ENRICHMENT_INPUT, f"the dataset has no analysis {wanted}")
        if run["status"] != "complete":
            raise StepError(EXIT_BAD_ENRICHMENT_INPUT,
                            f"analysis {wanted} is {run['status']}, not complete")
        return wanted
    complete = [r for r in runs if r["status"] == "complete"]
    if not complete:
        raise StepError(EXIT_BAD_ENRICHMENT_INPUT,
                        "the dataset has no complete differential expression analysis")
    return max(complete, key=lambda r: (r["completed_at"], r["id"]))["id"]


def de_document(*, dataset_id: int, de_run_id: int, genes: list[dict],
                comparisons: list[dict], rows: list[dict]) -> dict:
    """What enrich reads: the dataset's gene names and each comparison's genes."""
    names = {g["id"]: g["gene_name"] for g in genes}
    by_comparison: dict[int, list[dict]] = {c["id"]: [] for c in comparisons}
    for row in rows:
        by_comparison[row["de_id"]].append(
            {"gene": names[row["gene_id"]], "log2fc": row["log2fc"], "fdr": row["fdr"]})
    return {
        "dataset_id": dataset_id, "de_run_id": de_run_id,
        "genes": [g["gene_name"] for g in genes],
        "comparisons": [{"comparison": c["contrast"] or c["cluster_id"],
                         "genes": by_comparison[c["id"]]} for c in comparisons],
    }


def _all(build) -> list[dict]:
    """Every row of a query, a page at a time."""
    rows, start = [], 0
    while True:
        page = build().range(start, start + PAGE - 1).execute().data or []
        rows += page
        if len(page) < PAGE:
            return rows
        start += PAGE


# The pipeline's bloomctl credentials file, mounted from the environment's Secret.
CREDENTIALS = "/etc/bloom/credentials.txt"


def sign_in_home() -> None:
    """Gives bloomctl the pipeline's login: the mounted credentials copied into a private home,
    unless this home already has a bloomctl login (a hand run on a laptop)."""
    if (Path.home() / ".bloom" / "credentials.txt").is_file():
        return
    mounted = Path(os.environ.get("BLOOM_CREDENTIALS", CREDENTIALS))
    if not mounted.is_file() or mounted.stat().st_size == 0:
        raise StepError(EXIT_NO_CREDENTIALS, f"no Bloom credentials at {mounted}")
    home = Path(tempfile.mkdtemp())
    (home / ".bloom").mkdir(mode=0o700)
    copied = home / ".bloom" / "credentials.txt"
    shutil.copyfile(mounted, copied)
    copied.chmod(0o600)
    os.environ["HOME"] = str(home)


def _client() -> Any:
    """Bloom, signed in as bloomctl's default login."""
    sign_in_home()
    from bloomctl.credentials import DEFAULT_PROFILE
    from bloomctl.scrna import _session

    return _session.connect(DEFAULT_PROFILE).client


def fetch_de(dataset_id: int, wanted: int | None, fdr_max: float) -> dict:
    """Reads the dataset's genes and analysis."""
    client = _client()
    runs = _all(lambda: client.table("scrna_de_runs").select("id, status, completed_at")
                .eq("dataset_id", dataset_id).order("id"))
    de_run_id = pick_de_run(runs, wanted)
    genes = _all(lambda: client.table("scrna_genes").select("id, gene_name")
                 .eq("dataset_id", dataset_id).order("id"))
    comparisons = _all(lambda: client.table("scrna_de").select("id, cluster_id, contrast")
                       .eq("dataset_id", dataset_id).eq("run_id", de_run_id)
                       .eq("tested", True).order("id"))
    rows = []
    for comparison in comparisons:
        rows += _all(lambda c=comparison: client.table("scrna_de_genes")
                     .select("de_id, gene_id, log2fc, fdr").eq("de_id", c["id"])
                     .lt("fdr", fdr_max).order("id"))
    return de_document(dataset_id=dataset_id, de_run_id=de_run_id, genes=genes,
                       comparisons=comparisons, rows=rows)


# --------------------------------------------------------------------------- #
# The command
# --------------------------------------------------------------------------- #


def _tool(text: str) -> tuple[str, str]:
    name, sep, version = text.partition("=")
    if not sep or not name or not version:
        raise argparse.ArgumentTypeError(f"{text!r} is not name=version")
    return name, version


def _build_annotation(args) -> None:
    obo = go_terms.parse_obo(Path(args.obo).read_text(encoding="utf-8"))
    gtf = Path(args.gtf).read_text(encoding="utf-8").splitlines()
    if args.source == "eggnog":
        if not args.emapper:
            raise SystemExit("build-annotation --source eggnog needs --emapper")
        pathways = (go_terms.parse_kegg_list(Path(args.kegg_list).read_text(encoding="utf-8"))
                    if args.kegg_list else {})
        built = annotation.from_eggnog(
            Path(args.emapper).read_text(encoding="utf-8").splitlines(),
            transcript_genes=annotation.transcript_genes(gtf), ontology=obo, pathways=pathways)
    else:
        if not args.gaf:
            raise SystemExit("build-annotation --source tair-go needs --gaf")
        with gzip.open(args.gaf, "rt", encoding="utf-8") as fh:
            built = annotation.from_tair_gaf(fh, genome_genes=annotation.gtf_genes(gtf),
                                             ontology=obo)
    tools = dict(args.tool or [])
    tools["go_basic"] = obo.version
    if built.date:
        tools["tair_gaf"] = built.date
    annotation.write(Path(args.out), built, genome=args.genome, version=args.version,
                     source=args.source, tools=tools)
    print(f"{args.source}: {len(built.genes)} genes, {len(built.terms)} terms")


def _enrich(args) -> None:
    de = json.loads(Path(args.de).read_text(encoding="utf-8"))
    params = enrichment.Params(top_n=args.top_n, fdr_max=args.fdr_max, lfc_min=args.lfc_min,
                               min_term=args.min_term, max_term=args.max_term,
                               min_list=args.min_list)
    result = enrichment.run(annotation.read(Path(args.annotation)), dataset_genes=de["genes"],
                            comparisons=de["comparisons"], params=params)
    enrichment.write(Path(args.out), result, dataset_id=de["dataset_id"],
                     de_run_id=de["de_run_id"], annotation=args.label, params=params)
    print(f"{len(result.results)} significant terms; {result.annotated_genes} of "
          f"{result.background_genes} genes annotated")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="go-pathways")
    steps = parser.add_subparsers(dest="step", required=True)
    defaults = enrichment.Params()

    refs = steps.add_parser("fetch-references")
    refs.add_argument("--source", choices=SOURCES, required=True)
    refs.add_argument("--kegg-org", default="", help="KEGG organism, e.g. sbi (eggnog); "
                      "none: GO only")
    refs.add_argument("--to", required=True)

    fetch = steps.add_parser("fetch-de")
    fetch.add_argument("--dataset-id", type=int, required=True)
    fetch.add_argument("--de-run-id", type=int, help="default: the newest complete analysis")
    fetch.add_argument("--fdr-max", type=float, default=defaults.fdr_max)
    fetch.add_argument("--out", required=True)

    build = steps.add_parser("build-annotation")
    build.add_argument("--source", choices=SOURCES, required=True)
    build.add_argument("--emapper", help="eggNOG-mapper's .emapper.annotations (eggnog)")
    build.add_argument("--gaf", help="TAIR's tair.gaf.gz (tair-go)")
    build.add_argument("--kegg-list", help="KEGG's pathway list for the species (eggnog)")
    build.add_argument("--gtf", required=True)
    build.add_argument("--obo", required=True)
    build.add_argument("--genome", required=True)
    build.add_argument("--version", type=int, required=True)
    build.add_argument("--tool", type=_tool, action="append", help="name=version, repeatable")
    build.add_argument("--out", required=True)

    enrich = steps.add_parser("enrich")
    enrich.add_argument("--annotation", required=True)
    enrich.add_argument("--de", required=True)
    enrich.add_argument("--label", required=True, help="e.g. <genome>.v<N>/<source>-b<B>")
    enrich.add_argument("--out", required=True)
    for name in ("top_n", "min_term", "max_term", "min_list"):
        enrich.add_argument(f"--{name.replace('_', '-')}", type=int, default=getattr(defaults, name))
    for name in ("fdr_max", "lfc_min"):
        enrich.add_argument(f"--{name.replace('_', '-')}", type=float,
                            default=getattr(defaults, name))

    upload = steps.add_parser("upload-annotation")
    upload.add_argument("--file", required=True)

    fetch_build = steps.add_parser("fetch-annotation")
    fetch_build.add_argument("--build-id", type=int, required=True)
    fetch_build.add_argument("--out", required=True)
    fetch_build.add_argument("--label-out", required=True, help="where to write the build's name")

    record = steps.add_parser("upload-enrichment")
    record.add_argument("--file", required=True)
    record.add_argument("--build-id", type=int, required=True)
    record.add_argument("--rnaseq-run-id", type=int, help="the pipeline job, when it has one")

    args = parser.parse_args(argv)
    try:
        if args.step == "fetch-references":
            folder = Path(args.to)
            folder.mkdir(parents=True, exist_ok=True)
            for name, url in reference_urls(args.source, args.kegg_org).items():
                _download(url, folder / name)
                print(f"{name}: {url}")
        elif args.step == "fetch-de":
            doc = fetch_de(args.dataset_id, args.de_run_id, args.fdr_max)
            Path(args.out).write_text(json.dumps(doc), encoding="utf-8")
            print(f"analysis {doc['de_run_id']}: {len(doc['comparisons'])} comparisons")
        elif args.step == "build-annotation":
            _build_annotation(args)
        elif args.step == "enrich":
            _enrich(args)
        elif args.step == "upload-annotation":
            build = catalogue.upload_annotation(_client(), Path(args.file))
            print(f"annotation build {build['build']} (id {build['build_id']}): "
                  f"{build['file_path']}")
        elif args.step == "fetch-annotation":
            client = _client()
            build = catalogue.fetch_annotation(client, args.build_id, Path(args.out))
            name = catalogue.label(client, build)
            Path(args.label_out).write_text(name, encoding="utf-8")
            print(f"annotation build {args.build_id}: {name}")
        else:
            run = catalogue.upload_enrichment(_client(), Path(args.file), build_id=args.build_id,
                                              rnaseq_run_id=args.rnaseq_run_id)
            print(f"enrichment run {run['run_id']}: {run['file_path']}")
    except StepError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return error.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
