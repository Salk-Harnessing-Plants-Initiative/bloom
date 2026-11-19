"""`genome index` without the genome-index extra: the install instruction, not a traceback.

Kept apart from the recipe tests, which need pysam, so this runs where pysam is missing too."""

import builtins

import click
import pytest

from bloomctl.genome import _index


def test_without_the_extra_the_install_instruction_is_given(monkeypatch):
    real_import = builtins.__import__

    def no_pysam(name, *args, **kwargs):
        if name == "pysam":
            raise ImportError("No module named 'pysam'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pysam)
    with pytest.raises(click.ClickException, match=r"bloomctl\[genome-index\]"):
        _index.require_htslib()
