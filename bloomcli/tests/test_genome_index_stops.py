"""bloomctl genome index — temporary errors, stop signals, races, and what is said on stderr."""

import gzip
import importlib
import os
import signal

import httpx
import pytest
from genome_browser_fake import GENOME, GTF_GZ, stock
from genome_browser_fake import refusal as _refusal
from genome_browser_fake import replace_gtf as _replace_gtf
from genome_browser_fake import run_index as _run

from bloomctl.genome import _index

index_module = importlib.import_module("bloomctl.genome.index")

FOLDER = f"{GENOME}/v1/browser/b1"


@pytest.fixture
def db(env, storage):
    return stock(env, storage)


# --- temporary errors, stops and races ------------------------------------------------------


@pytest.mark.parametrize("code", ["57014", "55P03", "40001", "40P01", "PGRST003"])
def test_a_timeout_or_lock_error_exits_1_worth_retrying(db, code):
    db.fail["start_genome_browser_build"] = [
        _refusal("canceling statement due to statement timeout", code)]
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 1
    assert "run the same command again" in result.output


def _sigterm(*_args, **_kwargs):
    os.kill(os.getpid(), signal.SIGTERM)


def test_a_stop_signal_while_building_removes_the_downloads(db, tmp_path, monkeypatch):
    monkeypatch.setattr(_index, "build", _sigterm)
    work = tmp_path / "work"
    result = _run(f"{GENOME}.v1", "--workdir", str(work))
    assert result.exit_code != 0
    assert not any(work.iterdir())
    assert not db.called("start_genome_browser_build")


def test_a_stop_signal_while_uploading_abandons_the_build(db, monkeypatch):
    monkeypatch.setattr(index_module, "_upload", _sigterm)
    result = _run(f"{GENOME}.v1")
    assert result.exit_code != 0
    assert db.builds[0]["status"] == "abandoned"


def test_a_stop_while_starting_says_how_to_clear_a_build(db):
    db.fail["start_genome_browser_build"] = [KeyboardInterrupt()]
    result = _run(f"{GENOME}.v1")
    assert result.exit_code != 0
    assert "--replace-unfinished" in result.output


def test_a_start_that_loses_to_a_build_just_made_ready_reports_it(db):
    def finished_first(p_version_id, p_tool_versions):
        db.add_build("ready", gene_count=3)
        raise _refusal("genome version 101 already has browser build 1 (ready)", "55000")

    db.start_genome_browser_build = finished_first
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 0, result.output
    assert result.stdout == f"{GENOME}.v1 browser build b1 (id 7): {FOLDER}, 3 genes\n"


# --- what is said, and what Bloom refuses ---------------------------------------------------


def test_what_the_gtf_check_notes_is_said_and_temp_paths_are_not(db, storage):
    _replace_gtf(db, storage, gzip.decompress(GTF_GZ)
                 + b'Chr2\tAraport11\texon\t1\t4\t.\t+\t.\ttranscript_id "t9";\n')
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 0, result.output
    assert "1 GTF lines have no gene_id and are left out." in result.stderr
    assert "Downloaded tair10_araport11.v1's FASTA and GTF" in result.stderr
    assert "bloomctl-genome-index-" not in result.stderr


def test_a_version_withdrawn_before_the_finish_abandons_the_build(db, monkeypatch):
    real = index_module._add_genes

    def then_withdrawn(*args):
        real(*args)
        db.version["status"] = "withdrawn"

    monkeypatch.setattr(index_module, "_add_genes", then_withdrawn)
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 3
    assert "is withdrawn, not ready" in result.output
    assert db.builds[0]["status"] == "abandoned"


# --- edges ----------------------------------------------------------------------------------


def test_a_race_lost_to_a_ready_build_that_is_gone_again_exits_4(db):
    db.fail["start_genome_browser_build"] = [
        _refusal("genome version 101 already has browser build 1 (ready)", "55000")]
    assert _run(f"{GENOME}.v1").exit_code == 4


def test_a_version_withdrawn_before_the_start_is_refused_with_bloom_s_words(db):
    db.fail["start_genome_browser_build"] = [
        _refusal("genome version 101 is withdrawn, not ready", "55000")]
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 3
    assert "is withdrawn, not ready" in result.output


def test_a_start_that_returns_nothing_sends_nothing(db, monkeypatch):
    monkeypatch.setattr(db, "start_genome_browser_build", lambda **_: [])
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 1
    assert "did not return the new build" in result.output


def test_a_build_that_became_ready_while_its_abandon_failed_counts():
    class Bloom:
        """Uploading at the first look; the abandon fails; ready at the second look."""
        statuses = iter(["uploading", "ready"])

        def table(self, _name):
            status = next(self.statuses)
            row = type("R", (), {"data": [{"status": status}]})()
            chain = type("Q", (), {"select": lambda q, *_: q, "eq": lambda q, *_: q,
                                   "execute": lambda q: row})
            return chain()

        def rpc(self, *_):
            raise httpx.ConnectError("down")

    started = {"build": 1, "build_id": 7}
    assert index_module._abandon(Bloom(), started, f"{GENOME}.v1") == "ready"


def test_a_status_lookup_that_fails_is_unknown(db):
    class Down:
        def table(self, _name):
            raise httpx.ConnectError("down")

    assert index_module._status_of(Down(), 7) is None
