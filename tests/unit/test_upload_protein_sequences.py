"""Unit tests for scripts/uploaders/upload_protein_sequences.py's pure logic.

Covers the offline parts — FASTA parsing, the amino-acid alphabet preflight,
and uid construction. The PostgREST upsert paths need a live stack and are not
exercised here. tqdm is stubbed so the module imports without the runtime dep.

    uv run --extra test pytest tests/unit/test_upload_protein_sequences.py -v
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "uploaders" / "upload_protein_sequences.py"


def _load():
    # tqdm only drives the upsert progress bars; stub it so the FASTA/preflight
    # logic is importable without the dep (keeps this a pure unit test).
    if "tqdm" not in sys.modules:
        tq = types.ModuleType("tqdm")
        tq.tqdm = lambda x, **k: x
        sys.modules["tqdm"] = tq
    spec = importlib.util.spec_from_file_location("upload_protein_sequences", _SCRIPT)
    assert spec and spec.loader, f"cannot load {_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ups = _load()


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "seqs.faa"
    p.write_text(text)
    return p


# ---------------------------------------------------------------------------
# load_fasta_file
# ---------------------------------------------------------------------------

def test_parses_multiline_and_takes_gene_id_from_first_token(tmp_path):
    p = _write(tmp_path, ">AT5G16970.1 some description\nMASTA\nCVRRL\n>AT1G01010 x\nMKK*\n")
    assert ups.load_fasta_file(p) == {
        "AT5G16970.1": "MASTACVRRL",  # multi-line concatenated, description dropped
        "AT1G01010": "MKK*",          # stop codon '*' preserved
    }


def test_blank_lines_are_ignored(tmp_path):
    p = _write(tmp_path, ">G1\n\nMAST\n\nCVRR\n\n")
    assert ups.load_fasta_file(p) == {"G1": "MASTCVRR"}


def test_case_is_preserved(tmp_path):
    p = _write(tmp_path, ">G1\nMastCvrr\n")
    assert ups.load_fasta_file(p) == {"G1": "MastCvrr"}


def test_duplicate_gene_id_raises(tmp_path):
    p = _write(tmp_path, ">G1\nAAA\n>G1\nCCC\n")
    with pytest.raises(ValueError, match="duplicate gene_id"):
        ups.load_fasta_file(p)


def test_empty_sequence_raises(tmp_path):
    p = _write(tmp_path, ">G1\n>G2\nAAA\n")
    with pytest.raises(ValueError, match="empty sequence"):
        ups.load_fasta_file(p)


def test_no_records_raises(tmp_path):
    p = _write(tmp_path, "\n\n")
    with pytest.raises(ValueError, match="no FASTA records"):
        ups.load_fasta_file(p)


def test_sequence_before_header_raises(tmp_path):
    p = _write(tmp_path, "MASTACVRR\n>G1\nAAA\n")
    with pytest.raises(ValueError, match="before any header"):
        ups.load_fasta_file(p)


def test_empty_header_raises(tmp_path):
    p = _write(tmp_path, ">\nAAA\n")
    with pytest.raises(ValueError, match="empty FASTA header"):
        ups.load_fasta_file(p)


# ---------------------------------------------------------------------------
# make_uid
# ---------------------------------------------------------------------------

def test_make_uid_namespaced_by_default():
    assert ups.make_uid("arabidopsis", "AT5G16970.1", True) == "arabidopsis:AT5G16970.1"


def test_make_uid_no_namespace():
    assert ups.make_uid("arabidopsis", "AT5G16970.1", False) == "AT5G16970.1"


# ---------------------------------------------------------------------------
# preflight_check  (mirrors the protein_sequences.sequence CHECK)
# ---------------------------------------------------------------------------

def test_preflight_passes_standard_aas_lowercase_and_stop(capsys):
    # 20 AAs, ambiguity codes, lowercase, and stop '*' are all valid.
    ups.preflight_check({"G1": "ACDEFGHIKLMNPQRSTVWYBXZJUO", "G2": "mkk*"})
    assert "2 sequences clean" in capsys.readouterr().out


def test_preflight_rejects_gap_and_digit_naming_offenders():
    with pytest.raises(ValueError) as exc:
        ups.preflight_check({"G1": "MAST-CV", "G2": "MK9K"})
    msg = str(exc.value)
    assert "2 sequence(s)" in msg
    # the offending characters are surfaced so the user can fix the FASTA
    assert "-" in msg and "9" in msg


def test_preflight_rejects_whitespace_inside_sequence():
    with pytest.raises(ValueError, match="outside"):
        ups.preflight_check({"G1": "MAST CV"})
