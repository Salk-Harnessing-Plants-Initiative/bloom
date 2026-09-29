"""The writer (`batch-download-for-predict`) and the reader (`batch-ingest-result`'s
`discover_envelopes`) ship in one image, so they must agree on the run manifest's name for a
run identity — including after whitespace stripping (bloom #934; sleap-roots-pipeline#71
design section 3.2)."""

import json
import shutil

from click.testing import CliRunner
from sleap_roots_contracts import RUN_MANIFEST_FILENAME, RunManifest, pipeline_run_id_from_env
from test_cyl_download_for_predict import _patch_batch

import bloomctl.cyl.ingest as ing
from bloomctl.cli import cli


def _write_result(directory, scan_key):
    (directory / f"{scan_key}.result.json").write_text("{}", encoding="utf-8")


def test_the_reader_resolves_exactly_the_manifest_the_writer_wrote(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    monkeypatch.setenv("ARGO_WORKFLOW_NAME", " wf-e2e")
    stage = tmp_path / "stage"
    traits = tmp_path / "traits"
    traits.mkdir()

    result = CliRunner().invoke(cli, ["cyl", "batch-download-for-predict", str(stage), "--scan-ids", "1"])
    assert result.exit_code == 0, result.output

    written = list(stage.glob("run_manifest.*.json"))  # cannot match run_manifest.json
    assert len(written) == 1
    # Stand-in for predict/traits forwarding the manifest under the name they read.
    shutil.copy(written[0], traits / written[0].name)
    _write_result(traits, "scan_1")
    _write_result(traits, "scan_9")
    stale = RunManifest(pipeline_run_id="wf-old", scan_keys=["scan_9"])
    (stage / RUN_MANIFEST_FILENAME).write_text(stale.model_dump_json(), encoding="utf-8")
    (traits / RUN_MANIFEST_FILENAME).write_text(stale.model_dump_json(), encoding="utf-8")

    discovered = ing.discover_envelopes(traits, pipeline_run_id_from_env())

    assert [p.name for p in discovered.paths] == ["scan_1.result.json"]
    assert discovered.missing_scan_keys == []
    assert json.loads(written[0].read_text(encoding="utf-8"))["pipeline_run_id"] == "wf-e2e"
