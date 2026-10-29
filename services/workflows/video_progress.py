"""The progress callback a renderer reports through, and the stages it names.

Kept apart from the queue so the renderers depend on nothing but this.
"""

from __future__ import annotations

from typing import Callable

# The stages a job reports. The web pins these literals in its own tests.
STAGE_DOWNLOADING = "downloading"
STAGE_ENCODING = "encoding"
STAGE_UPLOADING = "uploading"

# (stage, frames done, frames total)
ProgressCallback = Callable[[str, "int | None", "int | None"], None]


def no_progress(stage: str, done: int | None = None, total: int | None = None) -> None:
    """The default for a render nobody is watching."""
