"""`bloomctl cyl download` batch selectors: --plant-qr-code (repeatable),
--barcodes-file, --accession-id. Query-shape + CLI wiring with monkeypatch fakes."""

from click.testing import CliRunner
from test_download_metadata import SCAN

import bloomctl.auth as auth
import bloomctl.cyl.download as dl
from bloomctl.cli import cli
from bloomctl.credentials import Credentials

# --- read_barcodes_file (pure) ---------------------------------------------


def test_read_barcodes_file_mixed_delimiters_and_comments(tmp_path):
    p = tmp_path / "bc.txt"
    p.write_text("QR-1, QR-2\nQR-3  QR-1\n# a comment\n\nQR-4 # trailing\n")
    assert dl.read_barcodes_file(p) == ["QR-1", "QR-2", "QR-3", "QR-4"]


def test_read_barcodes_file_empty(tmp_path):
    p = tmp_path / "bc.txt"
    p.write_text("\n#only a comment\n   \n")
    assert dl.read_barcodes_file(p) == []


# --- fetch_scans query shape ------------------------------------------------


class _CaptureQuery:
    def __init__(self, captured):
        self._c = captured
        self._c.setdefault("in_", [])

    def select(self, *a):
        return self

    def eq(self, col, val):
        self._c[("eq", col)] = val
        return self

    def in_(self, col, vals):
        self._c["in_"].append((col, list(vals)))
        return self

    def gte(self, col, val):
        self._c[("gte", col)] = val
        return self

    def lte(self, col, val):
        self._c[("lte", col)] = val
        return self

    def limit(self, n):
        self._c["limit"] = n
        return self

    def execute(self):
        return type("R", (), {"data": [SCAN]})()


def _capture_client(captured):
    class _Client:
        def table(self, name):
            captured["table"] = name
            return _CaptureQuery(captured)

    return _Client()


def test_fetch_scans_barcodes_use_in():
    cap = {}
    dl.fetch_scans(_capture_client(cap), 17957, plant_qr_codes=["A1", "A2"])
    assert cap["table"] == "cyl_scans_extended"
    assert ("qr_code", ["A1", "A2"]) in cap["in_"]


def test_fetch_scans_accession_ids_use_in():
    cap = {}
    dl.fetch_scans(_capture_client(cap), 17957, accession_ids=[42, 43])
    assert ("accession_id", [42, 43]) in cap["in_"]


def test_fetch_scans_both_selectors_and_age_window():
    cap = {}
    dl.fetch_scans(
        _capture_client(cap),
        17957,
        plant_qr_codes=["A1"],
        accession_ids=[42],
        plant_age_min=7,
        plant_age_max=14,
    )
    cols = [c for c, _ in cap["in_"]]
    assert "qr_code" in cols and "accession_id" in cols
    assert cap[("gte", "plant_age_days")] == 7 and cap[("lte", "plant_age_days")] == 14


def test_fetch_scans_no_selectors_omits_in():
    cap = {}
    dl.fetch_scans(_capture_client(cap), 17957)
    assert cap["in_"] == []  # no batch selectors → no IN() filter


# --- chunking: large IN() lists are split so no request URL exceeds proxy limits ---


class _ChunkRecorder:
    """Records each qr_code IN() chunk and returns one row per barcode in the chunk."""

    def __init__(self, seen_chunks):
        self._chunks = seen_chunks
        self._codes = None

    def select(self, *a):
        return self

    def eq(self, *a):
        return self

    def gte(self, *a):
        return self

    def lte(self, *a):
        return self

    def in_(self, col, vals):
        if col == "qr_code":
            self._codes = list(vals)
            self._chunks.append(list(vals))
        return self

    def limit(self, n):
        return self

    def execute(self):
        rows = [{"scan_id": code, "qr_code": code} for code in (self._codes or [])]
        return type("R", (), {"data": rows})()


def test_fetch_scans_chunks_large_barcode_list():
    seen_chunks: list[list[str]] = []

    class _Client:
        def table(self, name):
            return _ChunkRecorder(seen_chunks)

    codes = [f"BC{i}" for i in range(250)]  # 250 > _IN_CHUNK (100) → 3 chunks
    rows = dl.fetch_scans(_Client(), 17957, plant_qr_codes=codes)

    assert len(seen_chunks) == 3
    assert [len(c) for c in seen_chunks] == [100, 100, 50]
    assert max(len(c) for c in seen_chunks) <= dl._IN_CHUNK
    # every barcode's row is merged into the result (de-duped by scan_id)
    assert {r["scan_id"] for r in rows} == set(codes)


def test_fetch_scans_chunks_accession_ids_when_no_barcodes():
    seen: list[list[int]] = []

    class _Q:
        def select(self, *a):
            return self

        def eq(self, *a):
            return self

        def gte(self, *a):
            return self

        def lte(self, *a):
            return self

        def in_(self, col, vals):
            seen.append(list(vals))
            self._vals = list(vals)
            return self

        def limit(self, n):
            return self

        def execute(self):
            return type("R", (), {"data": [{"scan_id": v} for v in self._vals]})()

    class _Client:
        def table(self, name):
            return _Q()

    ids = list(range(150))  # 150 > 100 → 2 chunks
    rows = dl.fetch_scans(_Client(), 17957, accession_ids=ids)
    assert [len(c) for c in seen] == [100, 50]
    assert {r["scan_id"] for r in rows} == set(ids)


def test_barcode_alias_reaches_fetch(tmp_path, monkeypatch):
    # --barcode is an alias of --plant-qr-code (same qr_code column).
    _login(monkeypatch)
    monkeypatch.setattr(dl, "fetch_genotypes", lambda c, ids: {})
    captured = {}

    def _fake_fetch(client, experiment_id, *, plant_qr_codes=None, accession_ids=None, **k):
        captured["qr"] = plant_qr_codes
        return [SCAN]

    monkeypatch.setattr(dl, "fetch_scans", _fake_fetch)
    monkeypatch.setattr(dl, "download_images", lambda *a, **k: dl.DownloadResult([]))

    out = tmp_path / "out"
    res = CliRunner().invoke(
        cli,
        ["cyl", "download", str(out), "--experiment-id", "1", "--meta-only", "--barcode", "QR-9"],
    )
    assert res.exit_code == 0, res.output
    assert captured["qr"] == ["QR-9"]


# --- CLI wiring -------------------------------------------------------------


def _login(monkeypatch):
    monkeypatch.setattr(
        "bloomctl.credentials.load_credentials",
        lambda *a, **k: Credentials("https://x/api", "KEY", "u@s.edu", "pw"),
    )
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: object())


def test_barcodes_option_and_file_merge_deduped(tmp_path, monkeypatch):
    _login(monkeypatch)
    monkeypatch.setattr(dl, "fetch_genotypes", lambda c, ids: {})
    captured = {}

    def _fake_fetch(client, experiment_id, *, plant_qr_codes=None, accession_ids=None, **k):
        captured["qr"] = plant_qr_codes
        captured["acc"] = accession_ids
        return [SCAN]

    monkeypatch.setattr(dl, "fetch_scans", _fake_fetch)
    monkeypatch.setattr(dl, "download_images", lambda *a, **k: dl.DownloadResult([]))

    bc = tmp_path / "bc.txt"
    bc.write_text("QR-2\nQR-3\nQR-1\n")  # QR-1 also passed via option → de-duped
    out = tmp_path / "out"
    res = CliRunner().invoke(
        cli,
        [
            "cyl",
            "download",
            str(out),
            "--experiment-id",
            "17957",
            "--meta-only",
            "--plant-qr-code",
            "QR-1",
            "--barcodes-file",
            str(bc),
            "--accession-id",
            "42",
            "--accession-id",
            "43",
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured["qr"] == ["QR-1", "QR-2", "QR-3"]  # option first, then file, de-duped
    assert captured["acc"] == [42, 43]


def test_no_selectors_passes_none(tmp_path, monkeypatch):
    _login(monkeypatch)
    monkeypatch.setattr(dl, "fetch_genotypes", lambda c, ids: {})
    captured = {}

    def _fake_fetch(client, experiment_id, *, plant_qr_codes=None, accession_ids=None, **k):
        captured["qr"] = plant_qr_codes
        captured["acc"] = accession_ids
        return [SCAN]

    monkeypatch.setattr(dl, "fetch_scans", _fake_fetch)
    monkeypatch.setattr(dl, "download_images", lambda *a, **k: dl.DownloadResult([]))

    out = tmp_path / "out"
    res = CliRunner().invoke(
        cli, ["cyl", "download", str(out), "--experiment-id", "17957", "--meta-only"]
    )
    assert res.exit_code == 0, res.output
    assert captured["qr"] is None and captured["acc"] is None


def test_batch_selector_rejected_with_scan_id(tmp_path, monkeypatch):
    _login(monkeypatch)
    out = tmp_path / "out"
    res = CliRunner().invoke(
        cli, ["cyl", "download", str(out), "--scan-id", "5", "--plant-qr-code", "QR-1"]
    )
    assert res.exit_code != 0
    assert "Batch selectors" in res.output


def test_accession_id_rejected_with_scan_id(tmp_path, monkeypatch):
    _login(monkeypatch)
    out = tmp_path / "out"
    res = CliRunner().invoke(
        cli, ["cyl", "download", str(out), "--scan-id", "5", "--accession-id", "42"]
    )
    assert res.exit_code != 0
    assert "Batch selectors" in res.output
