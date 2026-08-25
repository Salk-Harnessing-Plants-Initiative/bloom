"""Three places build a plate video's object key and none can import the others:
the web app, the workflows encoder, and scripts/render_plate_videos.py, which
already wrote objects under this layout. These read all three as text and check
they agree, because every way of drifting is silent.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_PATH_MODULE = REPO_ROOT / "web" / "lib" / "supabase" / "plate-video-path.ts"
WORKFLOWS_PATH_MODULE = (
    REPO_ROOT / "services" / "workflows" / "plate_video_path.py"
)
RENDER_SCRIPT = REPO_ROOT / "scripts" / "render_plate_videos.py"


def _web() -> str:
    return WEB_PATH_MODULE.read_text(encoding="utf-8")


def _workflows() -> str:
    return WORKFLOWS_PATH_MODULE.read_text(encoding="utf-8")


def _script() -> str:
    return RENDER_SCRIPT.read_text(encoding="utf-8")


def test_videos_bucket_agrees():
    web = re.search(r'GRAVISCAN_VIDEOS_BUCKET\s*=\s*"([^"]+)"', _web())
    assert web, f"no GRAVISCAN_VIDEOS_BUCKET in {WEB_PATH_MODULE.name}"

    # The service allows an env override; the default is what must match.
    service = re.search(
        r'VIDEOS_BUCKET\s*=\s*os\.environ\.get\(\s*"[^"]+"\s*,\s*"([^"]+)"\s*\)',
        _workflows(),
    )
    assert service, f"no VIDEOS_BUCKET default in {WORKFLOWS_PATH_MODULE.name}"

    script = re.search(r'target_bucket:\s*str\s*=\s*"([^"]+)"', _script())
    assert script, f"no target_bucket default in {RENDER_SCRIPT.name}"

    assert web.group(1) == service.group(1) == script.group(1), (
        f"videos bucket mismatch: web {web.group(1)!r}, "
        f"workflows {service.group(1)!r}, script {script.group(1)!r}"
    )


def test_images_bucket_agrees():
    web = re.search(r'GRAVISCAN_IMAGES_BUCKET\s*=\s*"([^"]+)"', _web())
    assert web, f"no GRAVISCAN_IMAGES_BUCKET in {WEB_PATH_MODULE.name}"

    service = re.search(
        r'IMAGES_BUCKET\s*=\s*os\.environ\.get\(\s*"[^"]+"\s*,\s*"([^"]+)"\s*\)',
        _workflows(),
    )
    assert service, f"no IMAGES_BUCKET default in {WORKFLOWS_PATH_MODULE.name}"

    script = re.search(r'source_bucket:\s*str\s*=\s*"([^"]+)"', _script())
    assert script, f"no source_bucket default in {RENDER_SCRIPT.name}"

    assert web.group(1) == service.group(1) == script.group(1), (
        f"images bucket mismatch: web {web.group(1)!r}, "
        f"workflows {service.group(1)!r}, script {script.group(1)!r}"
    )


def test_plate_id_pattern_is_byte_identical():
    """Compared as a literal, not by behaviour: two regexes can agree on the
    cases someone thought of and still be different rules."""
    web = re.search(r'PLATE_ID_PATTERN\s*=\s*"([^"]+)"', _web())
    assert web, f"no PLATE_ID_PATTERN in {WEB_PATH_MODULE.name}"

    service = re.search(r'PLATE_ID_PATTERN\s*=\s*"([^"]+)"', _workflows())
    assert service, f"no PLATE_ID_PATTERN in {WORKFLOWS_PATH_MODULE.name}"

    assert web.group(1) == service.group(1), (
        f"plate id pattern mismatch:\n  web:       {web.group(1)!r}\n"
        f"  workflows: {service.group(1)!r}"
    )


def test_wave_segment_agrees():
    """Plate ids repeat across waves, so a wrong segment overwrites another
    wave's video."""
    web_named = re.search(r'return\s+`wave-\$\{waveNumber\}`', _web())
    assert web_named, f"{WEB_PATH_MODULE.name} does not build `wave-{{n}}`"

    service_named = re.search(r'return\s+f"wave-\{wave_number\}"', _workflows())
    assert service_named, f"{WORKFLOWS_PATH_MODULE.name} does not build `wave-{{n}}`"

    script_named = re.search(r'f"wave-\{job\.wave_number\}"', _script())
    assert script_named, f"{RENDER_SCRIPT.name} does not build `wave-{{n}}`"

    web_none = re.search(r'return\s+"(wave-[a-z]+)"', _web())
    service_none = re.search(r'return\s+"(wave-[a-z]+)"', _workflows())
    script_none = re.search(r'else\s+"(wave-[a-z]+)"', _script())
    assert web_none and service_none and script_none, (
        "each side must name the no-wave segment as a literal"
    )

    assert web_none.group(1) == service_none.group(1) == script_none.group(1), (
        f"no-wave segment mismatch: web {web_none.group(1)!r}, "
        f"workflows {service_none.group(1)!r}, script {script_none.group(1)!r}"
    )


def test_key_shape_agrees():
    """Constants can agree while the parts are assembled in a different order."""
    web = re.search(
        r'return\s+`\$\{experimentId\}/\$\{wave\}/\$\{plateId\}\.mp4`', _web()
    )
    assert web, f"{WEB_PATH_MODULE.name} does not build {{exp}}/{{wave}}/{{plate}}.mp4"

    service = re.search(
        r'return\s+f"\{experiment_id\}/\{wave\}/\{plate_id\}\.mp4"', _workflows()
    )
    assert service, (
        f"{WORKFLOWS_PATH_MODULE.name} does not build {{exp}}/{{wave}}/{{plate}}.mp4"
    )

    script = re.search(
        r'f"\{job\.experiment_id\}/\{wave_seg\}/\{job\.plate_id\}\.mp4"', _script()
    )
    assert script, (
        f"{RENDER_SCRIPT.name} does not build {{exp}}/{{wave}}/{{plate}}.mp4"
    )
