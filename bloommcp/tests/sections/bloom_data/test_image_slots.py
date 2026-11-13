"""Images run a few at a time across calls; a call never starts work past its time."""

from __future__ import annotations

import threading
import time

import pytest
from read_file_data import installed, read

from bloom_mcp.data_access import image_slots as slots
from bloom_mcp.sections.bloom_data import read_file as rf

FRAME_IDS = (1, 9)


@pytest.fixture
def fakes(monkeypatch):
    with installed() as (tables, files):
        # Every frame row points at a good image, so all eight can convert.
        for row in tables.tables["cyl_images"]:
            row["object_path"] = "exp/10/1.png"
        yield tables, files


@pytest.fixture
def no_slots(monkeypatch):
    held = threading.BoundedSemaphore(1)
    held.acquire()
    monkeypatch.setattr(slots, "_slots", held)
    monkeypatch.setattr(slots, "SLOT_WAIT_SECONDS", 0.05)


def _downloads(files):
    return [c for c in files.calls if c[0] == "download"]


def _all_slots_free() -> bool:
    taken = [slots._slots.acquire(timeout=0) for _ in range(slots.MAX_IMAGE_SLOTS)]
    for ok in taken:
        if ok:
            slots._slots.release()
    return all(taken)


def test_images_are_downloaded_and_converted_in_parallel(fakes, monkeypatch):
    _, files = fakes
    together = threading.Barrier(slots.MAX_IMAGE_SLOTS, timeout=5)
    download = files.download

    def meet_then_download(bucket, path):
        together.wait()  # passes only when MAX_IMAGE_SLOTS downloads run at once
        return download(bucket, path)

    monkeypatch.setattr(files, "download", meet_then_download)
    result, images, _ = read("cyl_images", *range(1, slots.MAX_IMAGE_SLOTS + 1))
    assert [f.kind for f in result.files] == ["image"] * slots.MAX_IMAGE_SLOTS
    assert len(images) == slots.MAX_IMAGE_SLOTS


def test_results_keep_the_order_of_the_ids(fakes, monkeypatch):
    _, files = fakes
    download = files.download
    seen = []

    def slower_first(bucket, path):
        seen.append(path)
        time.sleep(0.2 if len(seen) == 1 else 0)
        return download(bucket, path)

    monkeypatch.setattr(files, "download", slower_first)
    result, _, labels = read("cyl_images", 3, 99, 1)
    assert [f.id for f in result.files] == [3, 99, 1]
    assert [f.kind for f in result.files] == ["image", "error", "image"]
    assert labels[0].startswith("cyl_images id 3,")


def test_no_more_than_the_slots_run_at_once(fakes, monkeypatch):
    _, files = fakes
    running, peak, lock = 0, 0, threading.Lock()
    download = files.download

    def counted(bucket, path):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return download(bucket, path)

    monkeypatch.setattr(files, "download", counted)
    results = []

    def one_call():
        results.append(read("cyl_images", *range(1, 9)))

    calls = [threading.Thread(target=one_call) for _ in range(3)]
    for c in calls:
        c.start()
    for c in calls:
        c.join()
    assert 1 < peak <= slots.MAX_IMAGE_SLOTS
    assert [len(images) for _, images, _ in results] == [8, 8, 8]


def test_a_busy_server_says_so_and_offers_the_bloom_page(fakes, no_slots):
    _, files = fakes
    result, images, _ = read("cyl_images", 1)
    (f,) = result.files
    assert f.kind == "page" and f.note == rf.BUSY_NOTE and images == []
    assert "upload" in f.note and _downloads(files) == []


def test_a_call_out_of_time_reads_nothing_more(fakes, monkeypatch):
    _, files = fakes
    monkeypatch.setattr(slots, "CALL_SECONDS", 0)
    result, images, _ = read("cyl_images", 1, 2)
    assert [f.note for f in result.files] == [rf.OUT_OF_TIME_NOTE] * 2
    assert images == [] and files.calls == []


def test_videos_need_no_slot(fakes, no_slots):
    result, _, _ = read("gravi_plate_videos", 1)
    assert result.files[0].kind == "link" and result.files[0].note is None


@pytest.mark.parametrize("failure", ["download", "truncated", "crash"])
def test_a_failure_inside_a_slot_gives_the_slot_back(fakes, monkeypatch, failure):
    tables, files = fakes
    if failure == "truncated":
        tables.tables["cyl_images"][0]["object_path"] = "exp/10/truncated.png"
    else:
        error = OSError("down") if failure == "download" else RuntimeError("bug")

        def fails(bucket, path):
            raise error

        monkeypatch.setattr(files, "download", fails)
    for _ in range(slots.MAX_IMAGE_SLOTS + 1):
        result, images, _ = read("cyl_images", 1)
        assert images == [] and result.files[0].kind in ("error", "page")
    assert _all_slots_free()


def test_slot_wait_never_passes_the_call_deadline(monkeypatch, no_slots):
    monkeypatch.setattr(slots, "SLOT_WAIT_SECONDS", 5)
    started = time.monotonic()
    with pytest.raises(slots.NoSlot) as err:
        with slots.image_slot(time.monotonic() + 0.1):
            pass
    assert err.value.reason == "time" and time.monotonic() - started < 1
