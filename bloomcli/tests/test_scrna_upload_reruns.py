"""`hdf5 upload` run again, interrupted, or handed a file that changes or misleads: every
refusal comes before anything is sent, and nothing a dataset already holds is lost."""

import importlib

import httpx
from click.testing import CliRunner
from scrna_fixtures import NORMALIZATION, write_h5ad
from test_scrna_cli import LOAD_OPTIONS, _run
from test_scrna_upload_load import _cells_of, _labelled, _writes

from bloomctl.cli import cli
from bloomctl.scrna import _object

upload_module = importlib.import_module("bloomctl.scrna.upload")


def _dataset(env) -> dict:
    (row,) = env["client"].tables["scrna_datasets"]
    return row


# --- running the same file again ----------------------------------------------------------


def test_the_same_command_again_is_already_loaded(tmp_path, env, storage):
    path = _labelled(tmp_path)
    labels = ("--genotype-column", "genotype", "--control", "Col-0", "--facet", "transgene")
    assert _run("upload", "--yes", *labels, str(path)).exit_code == 0
    again = _run("upload", "--yes", *labels, str(path))
    assert again.exit_code == 0, again.output
    assert "is already loaded from this file" in again.stdout


def test_a_new_label_on_a_loaded_dataset_points_to_add_labels(tmp_path, env, storage):
    path = _labelled(tmp_path)
    assert _run("upload", "--yes", str(path)).exit_code == 0
    requests, writes = len(storage.requests), len(_writes(env["client"]))
    result = _run("upload", "--yes", "--facet", "transgene", str(path))
    assert result.exit_code != 0
    assert "--facet would not be written. Pass --add-labels" in result.output
    assert "Nothing was sent." in result.output
    assert len(storage.requests) == requests and len(_writes(env["client"])) == writes


def test_another_annotation_on_a_loaded_dataset_is_refused(tmp_path, env, storage):
    path = _labelled(tmp_path)
    assert _run("upload", "--yes", str(path)).exit_code == 0
    result = _run("upload", "--yes", "--name", "MYB41", "--species", "Arabidopsis",
                  "--annotation", "genotype", str(path))
    assert result.exit_code != 0
    assert "was loaded with --annotation cell_type, not genotype" in result.output


def test_added_labels_keep_the_ones_the_cells_already_have(tmp_path, env, storage):
    path = _labelled(tmp_path)
    assert _run("upload", "--yes", "--facet", "transgene", str(path)).exit_code == 0
    add = ("--name", "MYB41", "--species", "Arabidopsis", "--annotation", "cell_type",
           "--add-labels")
    result = _run("upload", "--yes", *add, "--facet", "treat", str(path))
    assert result.exit_code == 0, result.output
    assert [c["facets"] for c in _cells_of(env["client"])] == [
        {"transgene": "False", "treat": "a"}, {"transgene": "True", "treat": "b"},
        {"transgene": "True", "treat": "a"}]
    assert _dataset(env)["metadata"]["load_options"]["facets"] == ["transgene", "treat"]

    sources = _run("upload", "--yes", *add, "--source-column", "source", str(path))
    assert sources.exit_code == 0, sources.output
    recorded = _dataset(env)["metadata"]["load_options"]
    assert recorded["facets"] == ["transgene", "treat"], "a later run keeps earlier labels"
    assert recorded["source_column"] == "source"
    assert recorded["annotation"] == "cell_type"


def test_the_order_facets_are_given_in_does_not_block_a_resume(tmp_path, env, storage):
    path = _labelled(tmp_path)
    env["client"].fail(lambda op, table, payload: op == "insert" and table == "scrna_cells",
                       httpx.ConnectError("refused"))
    assert _run("upload", "--yes", "--facet", "transgene", "--facet", "treat",
                str(path)).exit_code != 0
    resumed = _run("upload", "--yes", "--facet", "treat", "--facet", "transgene", str(path))
    assert resumed.exit_code == 0, resumed.output


# --- refusals that come before the question -----------------------------------------------


def test_a_missing_sample_column_names_the_option_to_use(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "S1.h5ad", obs_columns={"leiden": ["0", "1", "0"]})
    result = _run("upload", "--yes", "--name", "S1", "--species", "Arabidopsis",
                  "--annotation", "leiden", "--create", str(path))
    assert result.exit_code != 0
    assert "has no obs['sample'], the column --sample-column names. Found: leiden" in result.output
    assert storage.requests == []


def test_sample_names_the_database_cannot_hold_are_refused_before_sending(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "d.h5ad", obs_columns={
        "cell_type": ["A", "B", "A"], "sample": ["s" * 101, "s1", "s1"]})
    result = _run("upload", "--yes", str(path))
    assert result.exit_code != 0
    assert "sample names longer than 100 characters" in result.output
    assert storage.requests == []


def test_a_damaged_resume_is_refused_before_anything_is_sent(tmp_path, env, storage):
    path = _labelled(tmp_path)
    env["client"].fail(lambda op, table, payload: op == "insert" and table == "scrna_cells",
                       httpx.ConnectError("refused"))
    assert _run("upload", "--yes", str(path)).exit_code != 0
    for row in env["client"].tables["scrna_clusters"]:
        row["ordinal"] += 10
    requests = len(storage.requests)
    dry = _run("upload", "--dry-run", str(path))
    assert dry.exit_code != 0, "the dry run has to refuse what the load would"
    assert "holds the cell types" in dry.output
    real = _run("upload", "--yes", str(path))
    assert real.exit_code != 0 and "Nothing was sent." in real.output
    assert len(storage.requests) == requests


def test_the_species_is_named_as_typed_in_a_refusal(tmp_path, env, storage):
    result = _run("upload", "--yes", "--name", "MYB41", "--species", "arabidopsis",
                  "--annotation", "cell_type", str(write_h5ad(tmp_path / "d.h5ad")))
    assert "no dataset named 'MYB41' for Arabidopsis" in result.output


def test_a_file_that_changes_while_being_read_is_refused(tmp_path, env, storage, monkeypatch):
    stamps = iter([(1, 1), (2, 2)])
    monkeypatch.setattr(upload_module, "_stamp", lambda _file: next(stamps))
    result = _run("upload", "--yes", str(write_h5ad(tmp_path / "d.h5ad")))
    assert result.exit_code != 0
    assert "changed while it was being read" in result.output
    assert storage.requests == [] and _writes(env["client"]) == []


def test_a_file_anndata_cannot_read_is_named_in_the_refusal(tmp_path, env, storage, monkeypatch):
    import anndata

    def unreadable(*_args, **_kwargs):
        raise KeyError("No read method registered")

    monkeypatch.setattr(anndata, "read_h5ad", unreadable)
    result = _run("upload", "--yes", str(write_h5ad(tmp_path / "d.h5ad")))
    assert result.exit_code != 0
    assert "d.h5ad could not be read by anndata" in result.output
    assert "Nothing was sent." in result.output


def test_file_text_in_a_refusal_is_shown_escaped(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "d.h5ad", obs_columns={
        "cell_type": ["A", "B", "A"], "sample": ["s1", "s1", "s1"],
        "genotype": ["Col\x1b[2J0", "pF\nACT", "pF\nACT"]})
    result = CliRunner().invoke(
        cli, ["scrna", "hdf5", "upload", *LOAD_OPTIONS, "--yes", "--genotype-column",
              "genotype", "--control", "nope", str(path)], color=True)
    assert result.exit_code != 0
    assert "\x1b" not in result.output and "pF\nACT" not in result.output
    assert "Col\\x1b[2J0, pF\\nACT" in result.output


# --- the order of things, and what the dataset records --------------------------------------


def test_a_failed_send_writes_no_dataset_rows(tmp_path, env, storage, monkeypatch):
    real = storage.handle

    def refuse_creates(request):
        if request.method == "POST":
            return httpx.Response(500, text="storage backend unavailable")
        return real(request)

    monkeypatch.setattr(storage, "handle", refuse_creates)
    assert _run("upload", "--yes", str(write_h5ad(tmp_path / "d.h5ad"))).exit_code != 0
    assert env["client"].tables["scrna_datasets"] == []
    assert _writes(env["client"]) == []


def test_the_options_reach_the_rows(tmp_path, env, storage):
    path = _labelled(tmp_path)
    result = _run("upload", "--yes", "--sample-column", "treat", "--expression-units", "CPM",
                  str(path))
    assert result.exit_code == 0, result.output
    cells = _cells_of(env["client"])
    assert [c["replicate"] for c in cells] == ["a", "b", "a"]
    assert [c["cluster_id"] for c in cells] == ["Cortex", "Xylem", "Cortex"]
    assert _dataset(env)["expression_units"] == "CPM"


def test_the_normalization_is_recorded_and_names_the_units(tmp_path, env, storage):
    block = {**NORMALIZATION, "transform": "log2p"}
    result = _run("upload", "--yes", str(write_h5ad(tmp_path / "d.h5ad", normalization=block)))
    assert result.exit_code == 0, result.output
    assert "units          log2(x+1) normalised counts" in result.stderr
    dataset = _dataset(env)
    assert dataset["expression_units"] == "log2(x+1) normalised counts"
    assert dataset["metadata"]["normalization"]["transform"] == "log2p"
    assert dataset["metadata"]["expected_cells"] == 3


def test_an_interrupted_load_is_waited_out_on_the_next_run(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "d.h5ad")
    env["client"].fail(lambda op, table, payload: op == "insert" and table == "scrna_cells",
                       KeyboardInterrupt(), commit=True)
    stopped = _run("upload", "--yes", str(path))
    assert stopped.exit_code != 0
    assert "interrupted: the file is stored and the load stopped" in stopped.output
    assert "wait about 6 minutes" in stopped.output
    again = _run("upload", "--yes", str(path))
    assert again.exit_code != 0
    assert "may still be finishing on the server; wait" in again.output


def test_a_declined_question_keeps_an_upload_waiting_to_be_resumed(tmp_path, env, storage,
                                                                  monkeypatch):
    path = write_h5ad(tmp_path / "d.h5ad")
    staged = _object.stage(path, tmp_path / "stage")
    _object.save_upload(tmp_path / "stage", staged.fingerprint, "abc", staged.size,
                        "http://api.test")
    monkeypatch.setattr(upload_module, "_interactive", lambda: True)
    result = _run("upload", str(path), input="n\n")
    assert result.exit_code != 0
    assert _object.upload_recorded(tmp_path / "stage", staged.fingerprint)
    assert staged.gz_path.exists()


def test_a_terminal_closed_mid_load_is_noted_like_ctrl_c(tmp_path, env, storage):
    """SIGTERM or SIGHUP while cells are written: noted, then the command exits."""
    import os
    import signal

    client = env["client"]
    real = client._execute

    def terminated_mid_insert(query):
        result = real(query)  # the write reaches the server before the process is stopped
        if query.op == "insert" and query.table == "scrna_cells":
            os.kill(os.getpid(), signal.SIGTERM)
        return result

    client._execute = terminated_mid_insert
    before = signal.getsignal(signal.SIGTERM)
    stopped = _run("upload", "--yes", str(write_h5ad(tmp_path / "d.h5ad")))
    assert stopped.exit_code != 0
    assert "interrupted: the file is stored and the load stopped" in stopped.output
    assert signal.getsignal(signal.SIGTERM) is before, "the handler is put back"
    again = _run("upload", "--yes", str(write_h5ad(tmp_path / "d.h5ad")))
    assert "may still be finishing on the server; wait" in again.output
