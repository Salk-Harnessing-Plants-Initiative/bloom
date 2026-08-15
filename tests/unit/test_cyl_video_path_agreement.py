"""The encoder and the web app must name the same video object.

A scan's video lives at `videos/cyl-videos/{scan_id}.mp4`. services/workflows
writes it; the web app reads it. Neither can import the other, so the two
literals are pinned against each other here.

Getting this wrong is silent both ways: a web app looking in the wrong place
finds nothing and renders as "this scan has no video", and an encoder writing to
the wrong key reports success while producing something nothing reads.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_PATH_MODULE = REPO_ROOT / "web" / "lib" / "supabase" / "scan-video-path.ts"
WORKFLOWS_VIDEO = REPO_ROOT / "services" / "workflows" / "video.py"


def _web() -> str:
    return WEB_PATH_MODULE.read_text(encoding="utf-8")


def _workflows() -> str:
    return WORKFLOWS_VIDEO.read_text(encoding="utf-8")


def test_buckets_agree():
    web = re.search(r'VIDEOS_BUCKET\s*=\s*"([^"]+)"', _web())
    assert web, f"no VIDEOS_BUCKET in {WEB_PATH_MODULE.name}"

    # The service allows an env override; the default is what must match.
    service = re.search(
        r'VIDEOS_BUCKET\s*=\s*os\.environ\.get\(\s*"[^"]+"\s*,\s*"([^"]+)"\s*\)',
        _workflows(),
    )
    assert service, "no VIDEOS_BUCKET default in services/workflows/video.py"

    assert web.group(1) == service.group(1), (
        f"bucket mismatch: web says {web.group(1)!r}, "
        f"workflows defaults to {service.group(1)!r}"
    )


def test_path_prefixes_agree():
    web = re.search(r'return\s+`([^/`$]+)/\$\{scanId\}\.mp4`', _web())
    assert web, f"scanVideoPath in {WEB_PATH_MODULE.name} is not the expected shape"

    service = re.search(r'VIDEO_PATH_PREFIX\s*=\s*"([^"]+)"', _workflows())
    assert service, "no VIDEO_PATH_PREFIX in services/workflows/video.py"

    assert web.group(1) == service.group(1), (
        f"path prefix mismatch: web writes {web.group(1)!r}/, "
        f"workflows writes {service.group(1)!r}/"
    )


def test_workflows_builds_the_key_from_the_prefix():
    """A literal key elsewhere in video.py would leave the constants agreeing
    while the object written still diverged."""
    assert re.search(
        r'f"\{VIDEO_PATH_PREFIX\}/\{scan_id\}\.mp4"', _workflows()
    ), "video.py must build the object key from VIDEO_PATH_PREFIX"


def test_plant_scan_does_not_rehardcode_the_path():
    """The client component could not import the server-only helper, so it used
    to carry its own copy of both literals. It imports the shared module now."""
    plant_scan = (REPO_ROOT / "web" / "components" / "plant-scan.tsx").read_text(
        encoding="utf-8"
    )

    assert "scan-video-path" in plant_scan, (
        "plant-scan.tsx must import the shared bucket/path module"
    )
    assert "cyl-videos/" not in plant_scan, (
        "plant-scan.tsx re-hardcodes the video path instead of importing it"
    )
