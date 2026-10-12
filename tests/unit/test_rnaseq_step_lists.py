"""The run page's Cell Ranger steps (web/lib/rnaseq-runs.ts) are the status poller's
(services/workflows/rnaseq_status.py), in the same order, plus the retired steps earlier runs
still show. Kept in two languages, so this test is what keeps them the same."""

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB_STEPS = ROOT / "web" / "lib" / "rnaseq-runs.ts"
STATUS = ROOT / "services" / "workflows" / "rnaseq_status.py"


def _status_module():
    spec = importlib.util.spec_from_file_location("rnaseq_status_for_test", STATUS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _web_steps() -> list[str]:
    text = WEB_STEPS.read_text()
    block = text[text.index("export const CELLRANGER_STEPS = [") :]
    block = block[: block.index("] as const;")]
    return re.findall(r'\{ id: "([a-z0-9-]+)", label: "[^"]+" \}', block)


def test_the_run_page_lists_the_pollers_steps_in_order():
    status = _status_module()
    retired = set(status.RETIRED_CELLRANGER_STEPS)
    current = [s for s in _web_steps() if s not in retired]
    assert current == list(status.CELLRANGER_STEPS.values())


def test_the_run_page_still_lists_every_retired_step():
    status = _status_module()
    assert set(status.RETIRED_CELLRANGER_STEPS) <= set(_web_steps())


def test_the_web_list_was_read():
    assert len(_web_steps()) >= 10, "the regex no longer matches the web list's format"
