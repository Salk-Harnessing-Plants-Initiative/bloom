"""`bloomctl scrna hdf5 upload`: store a dataset's h5ad under its fingerprint, then load it.

Every check runs before the question: the file's structure, its cells and labels, the size,
the normalization record, and what the load will do to the dataset. The file is stored first,
so a dataset row never names a file storage does not hold; then the cells are written.
"""

from __future__ import annotations

import signal
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import click

from ..credentials import DEFAULT_PROFILE
from . import (
    _cells,
    _counts,
    _format,
    _genes,
    _load,
    _object,
    _progress,
    _records,
    _send,
    _session,
    _summary,
    _transfer,
    _writer,
)
from ._text import visible


@click.command(name="upload")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--name", required=True,
              help="The dataset's name in Bloom: letters, digits, spaces, '.', '_' and '-'.")
@click.option("--species", required=True, help="The species' common name, e.g. Arabidopsis.")
@click.option("--annotation", required=True, metavar="COLUMN",
              help="The obs column holding each cell's cell-type or cluster label.")
@click.option("--create", is_flag=True, help="Register a new dataset under --name.")
@click.option("--sample-column", default="sample", show_default=True, metavar="COLUMN",
              help="The obs column naming each cell's sample.")
@click.option("--umap-key", metavar="KEY",
              help="The obsm array holding the UMAP, when it is not X_umap.")
@click.option("--expect-cells", type=int, help="Refuse unless the file holds this many cells.")
@click.option("--expression-units", metavar="TEXT",
              help="What the expression values are, for the colour bar; by default it follows "
                   "the file's uns['normalization'].")
@click.option("--genotype-column", metavar="COLUMN",
              help="The obs column naming each cell's genotype; needs --control.")
@click.option("--control", metavar="GENOTYPE", help="Which genotype is the control.")
@click.option("--construct", multiple=True, metavar="GENOTYPE=NAME",
              help="The construct a transgenic line carries; repeatable.")
@click.option("--facet", multiple=True, metavar="COLUMN",
              help="An obs column of labels to filter the map by; repeatable.")
@click.option("--expect-nonzero", multiple=True, metavar="GENE=COUNT",
              help="Refuse unless this gene is non-zero in exactly this many cells, e.g. a "
                   "transgene's; repeatable.")
@click.option("--source-column", metavar="COLUMN",
              help="The obs column naming where each cell's label came from.")
@click.option("--add-labels", is_flag=True,
              help="Add genotypes, labels and sources to a dataset already loaded from this "
                   "file, keeping those it has; not with --create.")
@click.option("-p", "--profile", default=DEFAULT_PROFILE, show_default=True,
              help="Credentials profile to use.")
@click.option("-y", "--yes", is_flag=True,
              help="Go ahead without asking; needed when there is no terminal (scripts, CI).")
@click.option("--dry-run", is_flag=True,
              help="Sign in and run every check, then stop before sending or writing anything.")
def upload(file: Path, profile: str, yes: bool, dry_run: bool, **opts: Any) -> None:
    """Store a dataset's AnnData file (.h5ad) and load its cells and gene counts into Bloom.

    Needs a writer, admin or pipeline login. Every check runs first; then what the file holds and what
    the load will do are shown on the terminal, and it goes ahead once confirmed. A stopped
    upload or load continues when the same command is run again. Load a dataset from one
    terminal at a time.
    """
    options = _options(opts)
    if not dry_run and not yes and not _interactive():
        raise click.ClickException(
            "there is no terminal to ask for confirmation in. Pass --yes to go ahead without "
            "asking, or --dry-run to run the checks. Nothing was sent."
        )
    conn = _session.connect(profile)
    if conn.role not in _session.WRITE_ROLES:
        raise click.ClickException(
            f"this login signs in as {conn.role or 'no role'}; uploading needs bloom_writer, "
            "bloom_admin or bloom_workflows. Nothing was read."
        )
    read_from = _stamp(file)
    summary = _checked(file, opts["umap_key"])
    cells, genotypes = _refused_unsent(lambda: _read(file, options, opts))
    genes = None if opts["add_labels"] else _refused_unsent(lambda: _genes_of(file, opts))
    species_id, species = _records.species(conn.client, opts["species"])
    stage = _object.staging_dir()
    writer = _writer.Writer(
        conn.client, lambda: _session.connect(profile).client,
        _writer.Marker(_writer.marker_path(stage, opts["name"])),
    )
    _refused_unsent(writer.marker.check)

    with _progress.track(f"Preparing {visible(file.name)}") as update:
        staged = _object.stage(file, stage, on_progress=update)
    try:
        if _stamp(file) != read_from:
            raise click.ClickException(
                f"{file.name} changed while it was being read; run the command again once it "
                "is saved. Nothing was sent."
            )
        normalization, recorded_on = _records.stored_file_checks(
            conn.client, file.name, staged, summary)
        options["expression_units"] = (
            options["expression_units"] or _records.units_for(normalization))
        plan = _refused_unsent(lambda: _load.plan(
            writer, opts["name"], species_id, cells, staged.fingerprint, options,
            create=opts["create"], add_labels=opts["add_labels"], species=species,
            counts=None if genes is None else (file, genes)))
        _show(_summary.describe(summary, file.name, recorded_on=recorded_on))
        _show(_summary.describe_load(cells, _dataset_text(plan, opts, species), genotypes,
                                     tuple(options["facets"] or ()),
                                     units=options["expression_units"],
                                     checked=_genes.parse_expectations(opts["expect_nonzero"]),
                                     counts=_counts_text(plan, opts)))
        if dry_run:
            click.echo("Dry run — every check passed. Nothing was sent or written.")
            raise click.exceptions.Exit(0)
        if not yes and not click.confirm(_question(plan, opts["name"]), default=False, err=True):
            click.echo("Nothing was sent.", err=True)
            raise click.exceptions.Exit(1)
    except BaseException:
        _release(stage, staged)
        raise

    uploading = _progress.track(f"Uploading {visible(file.name)}")
    with _transfer.open_client() as http, uploading as update:
        _send.send_through_expiry(http, conn, profile, stage, staged, file.name,
                                  on_progress=update)
    _write(writer, opts, species_id, species, cells, staged.fingerprint, options, normalization,
           None if genes is None else (file, genes), _unchanged_since(file, read_from),
           plan.genes)


def _options(opts: dict[str, Any]) -> dict[str, Any]:
    """The load options as the dataset records them, refusing combinations that cannot work."""
    if opts["control"] and not opts["genotype_column"]:
        raise click.UsageError("--control names a genotype, so it needs --genotype-column.")
    if opts["add_labels"] and not (
        opts["source_column"] or opts["genotype_column"] or opts["facet"]
    ):
        raise click.UsageError(
            "--add-labels needs --source-column, --genotype-column or --facet to add."
        )
    if opts["add_labels"] and opts["create"]:
        raise click.UsageError("--add-labels adds to a loaded dataset; it cannot --create one.")
    try:
        constructs = _cells.parse_constructs(opts["construct"])
        _genes.parse_expectations(opts["expect_nonzero"])
    except _writer.LoadError as exc:
        raise click.UsageError(str(exc)) from exc
    return {
        "annotation": opts["annotation"], "sample_column": opts["sample_column"],
        "umap_key": opts["umap_key"] or _format.UMAP_KEY,
        "source_column": opts["source_column"],
        "expression_units": opts["expression_units"],
        "genotype_column": opts["genotype_column"], "control": opts["control"],
        "constructs": constructs or None,
        # Given twice, a column is one label.
        "facets": list(dict.fromkeys(opts["facet"])) or None,
    }


def _interactive() -> bool:
    return sys.stdin.isatty()


def _unchanged_since(file: Path, read_from: tuple[int, int]):
    """A check that the file is still the one that was read and stored, refusing if not."""
    def check() -> None:
        if _stamp(file) != read_from:
            raise _writer.LoadError(
                f"{file.name} changed while it was being uploaded, so nothing from the changed "
                "file was written. Put the original file back and run the same command again "
                "to continue, or upload the new file as a new dataset with another --name and "
                "--create"
            )
    return check


def _stamp(file: Path) -> tuple[int, int]:
    """The file's size and modification time, to tell whether it changed while being read."""
    st = file.stat()
    return st.st_size, st.st_mtime_ns


def _checked(file: Path, umap_key: str | None) -> _format.Summary:
    try:
        summary = _format.check_structure(file, umap_key=umap_key)
    except _format.MissingDependency as exc:
        raise click.ClickException(str(exc)) from exc
    except _format.FormatError as exc:
        raise click.ClickException(
            f"{visible(file.name)} does not meet Bloom's h5ad format: {visible(str(exc))}"
        ) from exc
    if summary.umap_key is None:
        held = ", ".join(visible(key) for key in summary.obsm) or "nothing"
        raise click.ClickException(
            f"{file.name} has no UMAP (obsm holds: {held}); loading needs one, since the "
            "explorer plots stored coordinates and never computes them. Nothing was sent."
        )
    return summary


def _read(file: Path, options: dict[str, Any], opts: dict[str, Any]) -> tuple[dict, list[dict]]:
    """The cells and genotypes the load would write."""
    try:
        cells = _cells.read_cells(
            file, options["annotation"], options["sample_column"], options["umap_key"],
            opts["expect_cells"], options["source_column"], options["genotype_column"],
            tuple(options["facets"] or ()),
        )
    except _format.MissingDependency as exc:
        raise click.ClickException(str(exc)) from exc
    genotypes = (
        _cells.genotype_rows(cells["genotypes"], options["control"], options["constructs"] or {})
        if cells["genotypes"] is not None else []
    )
    return cells, genotypes


def _refused_unsent(call):
    """Run a check that may refuse; a refusal here comes before anything is sent."""
    try:
        return call()
    except _writer.LoadError as exc:
        raise click.ClickException(f"{visible(str(exc))}. Nothing was sent.") from exc


def _genes_of(file: Path, opts: dict[str, Any]) -> list[str]:
    """The file's gene names, with any --expect-nonzero checked against its counts."""
    names = _genes.read_names(file)
    _genes.check_expectations(file, names, _genes.parse_expectations(opts["expect_nonzero"]))
    return names


def _dataset_text(plan: _load.Plan, opts: dict[str, Any], species: str) -> str:
    name = visible(opts["name"].strip())
    return {
        "register": f"{name} ({visible(species)}) — new, registered by this upload",
        "resume": f"{name} (id {plan.dataset_id}) — continues a load that stopped",
        "already loaded": f"{name} (id {plan.dataset_id}) — already loaded from this file",
        "add labels": f"{name} (id {plan.dataset_id}) — labels will be added to its cells",
        "add counts": f"{name} (id {plan.dataset_id}) — loaded without its counts; they "
                      "will be added, and it shows as incomplete until they are",
    }[plan.outcome]


def _counts_text(plan: _load.Plan, opts: dict[str, Any]) -> str | None:
    """How many genes' counts the load writes, and where."""
    if plan.outcome in ("already loaded", "add labels"):
        return None
    if not plan.genes:
        return "all stored; the dataset will be finished"
    folder = _counts.clean_dataset_name(opts["name"])
    dataset_id = plan.dataset_id or "<new id>"
    return (f"{plan.genes:,} gene{'' if plan.genes == 1 else 's'}, one object each under "
            f"scrna/counts/{visible(folder)}_{dataset_id}_/")


def _question(plan: _load.Plan, name: str) -> str:
    name = visible(name.strip())
    if plan.outcome == "already loaded":
        return "Upload this file? Its dataset is already loaded"
    if plan.outcome == "add labels":
        return f"Upload this file and add these labels to '{name}'?"
    if plan.outcome == "add counts":
        return f"Upload this file and add its counts to '{name}'?"
    return f"Upload this file and load its cells into '{name}'?"


def _write(writer, opts, species_id: int, species: str, cells: dict, fingerprint: str,
           options, normalization: dict | None, counts, unchanged, genes: int) -> None:
    """Load the cells and counts, or add the labels, now that the file is stored."""
    name = opts["name"].strip()
    try:
        with _interrupted_by_stop_signals():
            if opts["add_labels"]:
                with _progress.track("Labelling cells", _progress.CELLS) as update:
                    dataset_id, added = _load.add_labels(
                        writer, name, species_id, cells, fingerprint, options, species=species,
                        on_progress=update)
            else:
                dataset_id, stored, outcome = _load.load(
                    writer, name, species_id, cells, fingerprint, options,
                    create=opts["create"], species=species, normalization=normalization,
                    counts=counts, track=_progress.track, unchanged=unchanged)
    except _writer.LoadError as exc:
        raise click.ClickException(
            f"the file is stored, but loading it stopped: {visible(str(exc))}") from exc
    except KeyboardInterrupt:
        raise click.ClickException(
            "interrupted: the file is stored and the load stopped. A write may still be "
            f"finishing on the server, so wait {writer.marker.wait_text()}, then run the same "
            "command again to continue"
        ) from None
    if opts["add_labels"]:
        click.echo(
            f"Added labels to dataset {dataset_id} ({name!r}): {added['genotypes']} "
            f"genotypes, {added['cells']:,} cells labelled, {added['sources']} cell-type sources"
        )
    elif outcome == "already loaded":
        click.echo(f"Dataset {dataset_id} ({name!r}) is already loaded from this file: "
                   f"{stored:,} cells")
    elif outcome == "counts added":
        click.echo(f"Added the counts of {genes:,} gene{'' if genes == 1 else 's'} to dataset "
                   f"{dataset_id} ({name!r}), which was missing them" if genes else
                   f"Finished dataset {dataset_id} ({name!r}): every gene's counts are stored")
    else:
        click.echo(f"{outcome.capitalize()} dataset {dataset_id} ({name!r}): {stored:,} cells")


# What closing the terminal or terminating the process sends; a hard kill cannot be caught.
STOP_SIGNALS = tuple(
    getattr(signal, name) for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name)
)


@contextmanager
def _interrupted_by_stop_signals():
    """While the load writes, a stop signal is handled as Ctrl-C, so it is noted before exiting."""
    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    previous = {sig: signal.signal(sig, interrupt) for sig in STOP_SIGNALS}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _release(stage: Path, staged: _object.Staged) -> None:
    """Drop the gzipped form, unless an earlier upload of it is waiting to be resumed."""
    if not _object.upload_recorded(stage, staged.fingerprint):
        _object.clear(stage, staged.fingerprint)


def _show(lines: list[str]) -> None:
    """The summary goes to the terminal, beside the question, even when output is redirected."""
    for line in lines:
        click.echo(line, err=True)
