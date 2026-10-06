"""Showing text that came from a file or the database on the terminal, without letting it act."""

from __future__ import annotations


def visible(text: str) -> str:
    """``text`` with every non-printable character written as its escape, e.g. ``\\x1b``."""
    return "".join(
        c if c.isprintable() else c.encode("unicode_escape").decode("ascii") for c in text
    )


def listed(names) -> str:
    """Names joined for a message, each escaped."""
    return ", ".join(visible(str(n)) for n in names)
