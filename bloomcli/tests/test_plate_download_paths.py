"""`bloomctl plate download` — path layout, CSV columns and the resume manifest.

Pure helpers only: no client, no network. A plate scan holds exactly one image
(gravi_images is UNIQUE(scan_id)), so repetition comes from time — a continuous session
captures the same plate every cycle. The layout groups by plate and names by capture.
"""

from __future__ import annotations

import csv

import pytest

import bloomctl.plate.download as pd

SCAN = {
    "scan_id": 1,
    "plate_id": "PLATE-001",
    "capture_date": "2026-05-27T14:03:11+00:00",
    "uploaded_at": "2026-05-27T14:05:00+00:00",
    "cycle_number": 0,
    "grid_mode": "2x2",
    "plate_index": "A1",
    "resolution": 1200,
    "format": "jpeg",
    "wave_number": 3,
    "transplant_date": "2026-05-20T00:00:00+00:00",
    "custom_note": "north rig",
    "scanner_id": 7,
    "scanner_name": "GRAV-01",
    "phenotyper_id": 5,
    "session_id": 88,
    "scan_mode": "continuous",
    "experiment_id": 12,
    "experiment_name": "Gravi 2026-05",
    "system_name": "GRAV-01",
    "species_id": 3,
    "species_name": "Pennycress",
    "species_genus": "Thlaspi",
    "species_species": "arvense",
    "metadata_id": 55,
    "accession_id": 42,
    "accession_name": "Spring-32",
}

IMAGE = {"scan_id": 1, "object_path": "gravi/plate-001-c0.jpg", "file_size_bytes": 2048}


# --------------------------------------------------------------------------- #
# Path layout
# --------------------------------------------------------------------------- #


def test_plate_relative_dir_groups_by_wave_then_plate():
    assert pd.plate_relative_dir(SCAN) == "images/Wave3/PLATE-001"


def test_image_dest_names_the_file_by_cycle_and_capture():
    dest = pd.image_dest("/out", SCAN, IMAGE)
    assert dest.parent.as_posix().endswith("images/Wave3/PLATE-001")
    assert dest.name == "2026-05-27T14-03-11+00-00_c0000.jpg"


def test_a_continuous_session_sorts_in_capture_order():
    # The whole point of the layout: lexical order must match capture order, so a
    # gravitropism time series reads correctly off a plain directory listing.
    names = [
        pd.image_dest(
            "/out",
            {**SCAN, "cycle_number": c, "capture_date": f"2026-05-27T{14 + c:02d}:03:11+00:00"},
            IMAGE,
        ).name
        for c in range(3)
    ]
    assert names == sorted(names)
    assert len(set(names)) == 3


def test_capture_order_survives_two_sessions_in_one_plate_directory():
    """The case zero-padding cannot reach.

    `cycle_number` restarts at 1 every session, and a directory is keyed by (wave, plate),
    which spans sessions. Leading with the cycle interleaves the two time courses — day two's
    cycle 1 lands between day one's cycles 1 and 2 — and a monotonic gravitropic response
    still reads as a smooth curve afterwards. The wrong one.
    """
    names = [
        pd.image_dest(
            "/out",
            {**SCAN, "session_id": session, "cycle_number": cycle, "capture_date": stamp},
            IMAGE,
        ).name
        for session, cycle, stamp in [
            (1, 1, "2026-05-01T09:10:00+00:00"),
            (1, 2, "2026-05-01T09:20:00+00:00"),
            (1, 3, "2026-05-01T09:30:00+00:00"),
            (2, 1, "2026-05-02T09:10:00+00:00"),
            (2, 2, "2026-05-02T09:20:00+00:00"),
            (2, 3, "2026-05-02T09:30:00+00:00"),
        ]
    ]

    assert names == sorted(names), "two sessions interleaved in one directory listing"
    assert len(set(names)) == 6


def test_a_single_mode_capture_sorts_by_time_not_ahead_of_every_cycle():
    """A scan with no cycle has no prefix. Sorting those first would order by shape, not time."""
    continuous = pd.image_dest(
        "/out", {**SCAN, "cycle_number": 0, "capture_date": "2026-05-01T09:00:00+00:00"}, IMAGE
    ).name
    single = pd.image_dest(
        "/out", {**SCAN, "cycle_number": None, "capture_date": "2026-05-03T09:00:00+00:00"}, IMAGE
    ).name

    assert [continuous, single] == sorted([continuous, single])


def test_capture_order_survives_a_session_longer_than_nine_cycles():
    """The boundary a three-cycle test cannot see.

    Unpadded, `c10` sorts between `c1` and `c2`, so every session of ten cycles or more —
    which is to say every real continuous session — reads out of order in a directory
    listing, in ffmpeg's glob, and in ImageJ's image sequence import. A gravitropic response
    is monotonic, so the reordered series still looks like a smooth curve. It is the wrong
    curve, and nothing about it looks wrong.
    """
    names = [
        pd.image_dest(
            "/out",
            {
                **SCAN,
                "cycle_number": c,
                # One cycle every ten minutes, so the instants stay ordered too.
                "capture_date": f"2026-05-27T{14 + c // 6:02d}:{(c % 6) * 10:02d}:00+00:00",
            },
            IMAGE,
        ).name
        for c in range(24)
    ]

    assert names == sorted(names), "a directory listing no longer reads in capture order"
    assert len(set(names)) == 24


def test_single_mode_scan_has_no_cycle_prefix():
    dest = pd.image_dest("/out", {**SCAN, "cycle_number": None}, IMAGE)
    assert dest.name == "2026-05-27T14-03-11+00-00.jpg"


def test_extension_is_taken_from_the_object_not_hardcoded():
    dest = pd.image_dest("/out", SCAN, {**IMAGE, "object_path": "gravi/x.png"})
    assert dest.suffix == ".png"


def test_object_without_an_extension_falls_back_to_jpg():
    # gravi_scans.format defaults to 'jpeg' and the desktop uploads jpegs.
    dest = pd.image_dest("/out", SCAN, {**IMAGE, "object_path": "gravi/no-ext"})
    assert dest.suffix == ".jpg"


def test_colons_in_the_capture_timestamp_are_replaced():
    # "14:03" in a filename opens an alternate data stream on Windows instead of writing a file.
    assert ":" not in pd.image_dest("/out", SCAN, IMAGE).name


def test_missing_wave_still_produces_a_directory():
    # wave_number is nullable; the scan must be kept, not dropped.
    assert pd.plate_relative_dir({**SCAN, "wave_number": None}) == "images/WaveNone/PLATE-001"


def test_plate_id_with_separators_cannot_escape_the_output_dir(tmp_path):
    hostile = {**SCAN, "plate_id": "../../etc"}
    dest = pd.image_dest(tmp_path, hostile, IMAGE)
    assert tmp_path in dest.parents
    assert ".." not in dest.parts


def test_plate_id_that_is_only_dots_becomes_a_placeholder():
    assert pd.plate_relative_dir({**SCAN, "plate_id": ".."}) == "images/Wave3/_"


def test_absolute_looking_plate_id_stays_inside(tmp_path):
    dest = pd.image_dest(tmp_path, {**SCAN, "plate_id": "/etc/passwd"}, IMAGE)
    assert tmp_path in dest.parents


# --------------------------------------------------------------------------- #
# plates.csv
# --------------------------------------------------------------------------- #


def test_csv_columns_are_a_fixed_order():
    assert pd.CSV_COLUMNS[0] == "scan_id"
    assert "image_path" in pd.CSV_COLUMNS
    # Every view column the CLI promises to carry through.
    for column in ("plate_id", "capture_date", "experiment_name", "system_name", "scan_mode"):
        assert column in pd.CSV_COLUMNS


def test_build_plate_row_image_path_matches_the_destination(tmp_path):
    row = pd.build_plate_row(SCAN, IMAGE)
    assert (tmp_path / row["image_path"]) == pd.image_dest(tmp_path, SCAN, IMAGE)


def test_build_plate_row_carries_the_metadata(tmp_path):
    row = pd.build_plate_row(SCAN, IMAGE)
    assert row["experiment_name"] == "Gravi 2026-05"
    assert row["accession_name"] == "Spring-32"
    assert row["scan_mode"] == "continuous"
    assert row["scanner_name"] == "GRAV-01"


def test_build_plate_row_blank_for_missing_optional_metadata():
    bare = {k: v for k, v in SCAN.items() if k not in {"scanner_name", "scan_mode"}}
    row = pd.build_plate_row(bare, IMAGE)
    assert row["scanner_name"] == ""
    assert row["scan_mode"] == ""


def test_build_plate_row_without_an_image_has_no_image_path():
    # A scan whose upload was interrupted has no gravi_images row; it still gets a CSV row.
    assert pd.build_plate_row(SCAN, None)["image_path"] == ""


def test_build_plate_row_survives_an_image_row_it_cannot_path():
    # plates.csv is written before anything is fetched, so raising here would abort the whole
    # run over one bad row — while the download itself records that row and carries on.
    row = pd.build_plate_row(SCAN, {"scan_id": 1})  # no object_path
    assert row["image_path"] == ""
    assert row["scan_id"] == 1, "the rest of the row is still written"


def test_write_plates_csv_roundtrip(tmp_path):
    path = tmp_path / "plates.csv"
    pd.write_plates_csv([pd.build_plate_row(SCAN, IMAGE)], path)
    with path.open() as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == pd.CSV_COLUMNS
        rows = list(reader)
    assert len(rows) == 1 and rows[0]["plate_id"] == "PLATE-001"


# --------------------------------------------------------------------------- #
# plate_sections.csv
# --------------------------------------------------------------------------- #

SECTIONS = [
    {"metadata_id": 55, "plate_section_id": "top", "medium": "MS", "plant_qr": "QR-1"},
    {"metadata_id": 55, "plate_section_id": "top", "medium": "MS", "plant_qr": "QR-2"},
    {"metadata_id": 55, "plate_section_id": "bottom", "medium": "MS+NaCl", "plant_qr": "QR-3"},
]


def test_section_rows_are_one_per_section_and_plant():
    rows = pd.build_section_rows(SECTIONS)
    assert len(rows) == 3
    assert rows[0]["plate_section_id"] == "top" and rows[0]["plant_qr"] == "QR-1"
    assert rows[2]["medium"] == "MS+NaCl"


def test_section_rows_join_back_to_plates_csv_on_metadata_id():
    plate = pd.build_plate_row(SCAN, IMAGE)
    rows = pd.build_section_rows(SECTIONS)
    assert all(str(r["metadata_id"]) == str(plate["metadata_id"]) for r in rows)


def test_write_sections_csv_roundtrip(tmp_path):
    path = tmp_path / "plate_sections.csv"
    pd.write_sections_csv(pd.build_section_rows(SECTIONS), path)
    with path.open() as fh:
        assert csv.DictReader(fh).fieldnames == pd.SECTION_COLUMNS


# --------------------------------------------------------------------------- #
# Resume manifest
# --------------------------------------------------------------------------- #


def test_selector_records_every_filter():
    selector = pd.download_selector(
        experiment_id=12, scan_id=None, plate_id="P1", wave_number=3, session_id=88, limit=100
    )
    assert selector == {
        "experiment_id": 12,
        "scan_id": None,
        "plate_id": "P1",
        "wave_number": 3,
        "session_id": 88,
        "limit": 100,
    }


def test_resolving_a_name_to_an_id_is_the_same_selection():
    # Selecting by --experiment-name on one run and --experiment-id on the next must resume,
    # so the selector records the resolved id and never the typed name.
    by_name = pd.download_selector(experiment_id=12, scan_id=None, limit=100)
    by_id = pd.download_selector(experiment_id=12, scan_id=None, limit=100)
    assert pd.describe_manifest_mismatch(by_name, by_id) == ""


def test_a_different_filter_is_reported_field_by_field():
    first = pd.download_selector(experiment_id=12, wave_number=3)
    second = pd.download_selector(experiment_id=12, wave_number=4)
    mismatch = pd.describe_manifest_mismatch(first, second)
    assert "wave_number" in mismatch and "3" in mismatch and "4" in mismatch


def test_a_failed_plates_csv_write_leaves_the_previous_one_intact(tmp_path, monkeypatch):
    """The images survive a full disk; the metadata that makes them interpretable must too.

    plates.csv is rewritten on every run, including a resume. Opening it for writing empties
    it before the first row lands, so a failure there destroys the previous run's copy and
    leaves a tree of images with nothing describing them.
    """
    import bloomctl._storage as storage

    path = tmp_path / "plates.csv"
    pd.write_plates_csv([{name: "first" for name in pd.CSV_COLUMNS}], path)
    before = path.read_bytes()

    def _no_space(dest, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(storage, "atomic_write_bytes", _no_space)
    monkeypatch.setattr(pd, "atomic_write_bytes", _no_space)

    with pytest.raises(OSError):
        pd.write_plates_csv([{name: "second" for name in pd.CSV_COLUMNS}], path)

    assert path.read_bytes() == before, "the previous run's metadata was destroyed"
    assert not list(tmp_path.glob(".dl-*.tmp")), "left a temp file behind"


def test_a_failed_sections_csv_write_leaves_the_previous_one_intact(tmp_path, monkeypatch):
    import bloomctl._storage as storage

    path = tmp_path / "plate_sections.csv"
    pd.write_sections_csv([{name: "first" for name in pd.SECTION_COLUMNS}], path)
    before = path.read_bytes()

    def _no_space(dest, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(storage, "atomic_write_bytes", _no_space)
    monkeypatch.setattr(pd, "atomic_write_bytes", _no_space)

    with pytest.raises(OSError):
        pd.write_sections_csv([{name: "second" for name in pd.SECTION_COLUMNS}], path)

    assert path.read_bytes() == before
