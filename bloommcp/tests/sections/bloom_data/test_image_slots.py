"""Images run a few at a time across calls; a call never waits past its time."""

from __future__ import annotations

import threading
import time

import pytest

from bloom_mcp.data_access import image_slots as slots
from bloom_mcp.data_access.fake_file_reader import FakeFileReader
from bloom_mcp.sections.bloom_data import read_file as rf
from bloom_mcp.tools import _ports
from test_read_file import FRAME, _read


@pytest.fixture
def frames():
    fake = FakeFileReader({("images", f"s/{i}.png"): FRAME for i in range(8)})
    previous = _ports.file_reader()
    _ports.configure(file_reader=fake)
    yield fake
    _ports.configure(file_reader=previous)


@pytest.fixture
def no_slots(monkeypatch):
    held = threading.BoundedSemaphore(1)
    held.acquire()
    monkeypatch.setattr(slots, "_slots", held)
    monkeypatch.setattr(slots, "SLOT_WAIT_SECONDS", 0.05)


def test_images_are_downloaded_and_converted_in_parallel(frames, monkeypatch):
    together = threading.Barrier(slots.MAX_IMAGE_SLOTS, timeout=5)
    download = frames.download

    def meet_then_download(bucket, path):
        together.wait()  # passes only when MAX_IMAGE_SLOTS downloads run at once
        return download(bucket, path)

    monkeypatch.setattr(frames, "download", meet_then_download)
    paths = [f"s/{i}.png" for i in range(slots.MAX_IMAGE_SLOTS)]
    result, images = _read("images", *paths)
    assert [f.kind for f in result.files] == ["image"] * len(paths)
    assert len(images) == len(paths)


def test_results_keep_the_order_of_the_paths(frames, monkeypatch):
    download = frames.download

    def slower_first(bucket, path):
        time.sleep(0.2 if path == "s/0.png" else 0)
        return download(bucket, path)

    monkeypatch.setattr(frames, "download", slower_first)
    paths = ["s/0.png", "s/missing.png", "s/2.png"]
    result, images = _read("images", *paths)
    assert [f.path for f in result.files] == paths
    assert [f.kind for f in result.files] == ["image", "error", "image"]
    assert len(images) == 2


def test_no_more_than_the_slots_run_at_once(frames, monkeypatch):
    running, peak, lock = 0, 0, threading.Lock()
    download = frames.download

    def counted(bucket, path):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return download(bucket, path)

    monkeypatch.setattr(frames, "download", counted)
    calls = [
        threading.Thread(
            target=_read, args=("images", *[f"s/{i}.png" for i in range(8)])
        )
        for _ in range(3)
    ]
    for c in calls:
        c.start()
    for c in calls:
        c.join()
    assert 1 < peak <= slots.MAX_IMAGE_SLOTS


def test_a_busy_server_says_so_and_offers_the_bloom_page(frames, no_slots):
    result, images = _read("images", "s/0.png")
    (f,) = result.files
    assert f.kind == "link" and f.note == rf.BUSY_NOTE and images == []
    assert "upload" in f.note and not [c for c in frames.calls if c[0] == "download"]


def test_a_call_out_of_time_starts_no_new_image(frames, monkeypatch):
    monkeypatch.setattr(slots, "CALL_SECONDS", 0)
    result, images = _read("images", "s/0.png", "s/1.png")
    assert [f.note for f in result.files] == [rf.OUT_OF_TIME_NOTE] * 2
    assert images == [] and not [c for c in frames.calls if c[0] == "download"]


def test_videos_need_no_slot(frames, no_slots):
    frames.files[("videos", "1.mp4")] = b"\0" * 5
    result, _ = _read("videos", "1.mp4")
    assert result.files[0].kind == "link" and result.files[0].note is None


def test_a_failed_image_gives_its_slot_back(frames):
    frames.broken.add(("images", "s/0.png"))
    for _ in range(slots.MAX_IMAGE_SLOTS + 1):
        _read("images", "s/0.png")
    for _ in range(slots.MAX_IMAGE_SLOTS):
        assert slots._slots.acquire(timeout=0)
    for _ in range(slots.MAX_IMAGE_SLOTS):
        slots._slots.release()


def test_slot_wait_never_passes_the_call_deadline(monkeypatch, no_slots):
    monkeypatch.setattr(slots, "SLOT_WAIT_SECONDS", 5)
    started = time.monotonic()
    with pytest.raises(slots.NoSlot) as err:
        with slots.image_slot(time.monotonic() + 0.1):
            pass
    assert err.value.reason == "time" and time.monotonic() - started < 1
