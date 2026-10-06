"""Progress for the upload's long steps: each step reports how far it is, up to its total."""

from scrna_fake_db import database
from scrna_fixtures import write_h5ad
from test_scrna_load import OPTIONS, cells, writer

from bloomctl.scrna import _load, _object, _progress


def test_preparing_the_file_reports_every_byte_read(tmp_path):
    path = write_h5ad(tmp_path / "d.h5ad")
    seen = []
    _object.stage(path, tmp_path / "stage", on_progress=lambda done, total: seen.append((done, total)))
    size = path.stat().st_size
    assert seen and seen[-1] == (size, size)
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)


def test_writing_cells_reports_each_batch_up_to_the_last_cell(tmp_path, monkeypatch):
    monkeypatch.setattr(_load, "CELL_BATCH", 2)
    seen = []
    _load.load(writer(database(), tmp_path), "MYB41", 1, cells(("A", "B", "A", "B", "C")),
               "sha-1", OPTIONS, create=True, on_progress=lambda d, t: seen.append((d, t)))
    assert seen == [(2, 5), (4, 5), (5, 5)]


def test_a_resumed_load_counts_only_the_cells_still_to_write(tmp_path, monkeypatch):
    monkeypatch.setattr(_load, "CELL_BATCH", 2)
    client = database()
    real = _load._insert_cells

    def stop_after_first_batch(w, dataset_id, table, missing, genotype_ids=None, on_progress=None):
        real(w, dataset_id, table, missing[:2], genotype_ids)
        raise KeyboardInterrupt

    monkeypatch.setattr(_load, "_insert_cells", stop_after_first_batch)
    try:
        _load.load(writer(client, tmp_path), "MYB41", 1, cells(("A", "B", "A", "B", "C")),
                   "sha-1", OPTIONS, create=True)
    except KeyboardInterrupt:
        pass
    monkeypatch.setattr(_load, "_insert_cells", real)
    seen = []
    _load.load(writer(client, tmp_path), "MYB41", 1, cells(("A", "B", "A", "B", "C")),
               "sha-1", OPTIONS, on_progress=lambda d, t: seen.append((d, t)))
    assert seen == [(2, 3), (3, 3)]


def test_progress_is_silent_when_the_terminal_is_not_one(capsys):
    with _progress.track("Uploading d.h5ad") as update:
        update(5, 10)
    assert capsys.readouterr().err == ""
