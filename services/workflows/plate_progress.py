"""Where the running plate render has got to.

One render at a time (MAX_CONCURRENT_ENCODES), so one slot. Advisory only: the
page falls back to a plain wait when there is nothing here.
"""

import threading

_lock = threading.Lock()
_current: dict | None = None


def start(experiment_id: int, plate_id: str, wave_number: int | None) -> None:
    global _current
    with _lock:
        _current = {
            "experiment_id": experiment_id,
            "plate_id": plate_id,
            "wave_number": wave_number,
            "stage": "downloading",
            "done": 0,
            "total": 0,
        }


def advance(stage: str, done: int, total: int) -> None:
    with _lock:
        if _current is not None:
            _current.update(stage=stage, done=done, total=total)


def finish() -> None:
    global _current
    with _lock:
        _current = None


def current(experiment_id: int, plate_id: str, wave_number: int | None) -> dict | None:
    with _lock:
        if _current is None:
            return None
        if (
            _current["experiment_id"] != experiment_id
            or _current["plate_id"] != plate_id
            or _current["wave_number"] != wave_number
        ):
            return None
        return {
            "stage": _current["stage"],
            "done": _current["done"],
            "total": _current["total"],
        }
