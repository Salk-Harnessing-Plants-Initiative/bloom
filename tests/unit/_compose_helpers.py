"""Readers for compose values that more than one guard needs.

Extracted from test_workflows_single_worker.py so the video-worker guards can
reuse them without a test-module-imports-test-module coupling, matching
_workflow_helpers.py's precedent.
"""

from __future__ import annotations


def _bytes(value) -> int:
    """A compose size as bytes. `2g`, `2G`, `2048m` and 2147483648 are one limit.

    Parsed rather than string-matched so a correct edit in a different spelling
    is not reported as a broken one — a test that accepts only the spelling it
    was written against is a trap for whoever touches the file next.
    """
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    # Compose accepts b/kb/mb/gb as well as the bare letter, so `2gb` and `2g`
    # are one limit. Dropping a trailing `b` first keeps the lookup to one form.
    if text.endswith("b") and len(text) > 1 and text[-2].isalpha():
        text = text[:-1]
    for suffix, scale in (("g", 1 << 30), ("m", 1 << 20), ("k", 1 << 10), ("b", 1)):
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * scale)
    return int(text)


def _mount_options(entry: str) -> dict[str, str]:
    """The options of a tmpfs mount, by name — order is not meaning."""
    _, _, options = entry.partition(":")
    parsed = {}
    for option in options.split(","):
        name, _, value = option.partition("=")
        parsed[name] = value
    return parsed
