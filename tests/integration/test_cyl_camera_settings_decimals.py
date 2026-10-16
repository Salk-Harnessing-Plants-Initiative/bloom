"""
Integration tests for bloom#1119: insert_image_v3_0 accepts decimal camera settings and
stores the frame count.

Before 20261016120000_widen_cyl_camera_settings_to_double, exposure time, gain, brightness,
contrast and seconds per rotation were integer, so an upload with brightness 0.5 failed with
22P02 and the image was not stored. num_frames was accepted but not stored.

Most tests run as `pg_conn` (supabase_admin) against the database CI has migrated, and roll
back. One test calls the RPC through PostgREST, the path uploads take, and writes no rows.
"""

import json
import os
import urllib.error
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

CAMERA_COLUMNS = (
    "scanner_exposure_time",
    "scanner_gain",
    "scanner_brightness",
    "scanner_contrast",
    "scanner_gamma",
    "scanner_seconds_per_rot",
)
SETTINGS = {
    "exposure_time": 9000,
    "gain": 150,
    "brightness": 0.5,
    "contrast": 0,
    "gamma": 1.0,
    "seconds_per_rot": 7.5,
}


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as cursor:
        yield cursor
    pg_conn.rollback()


@pytest.fixture
def seeded(cur):
    """A species and scanner the RPC can find, with names unique to this test."""
    tag = uuid.uuid4().hex[:8]
    species, scanner = f"species-{tag}", f"scanner-{tag}"
    cur.execute("INSERT INTO species (common_name) VALUES (%s)", (species,))
    cur.execute("INSERT INTO cyl_scanners (name) VALUES (%s)", (scanner,))
    return {"species": species, "scanner": scanner, "tag": tag}


def _insert_image(
    cur,
    seeded,
    plant,
    *,
    exposure_time,
    gain,
    brightness,
    contrast,
    gamma,
    seconds_per_rot,
    num_frames=72,
):
    """Call insert_image_v3_0 directly by argument name and return the image id.

    This skips PostgREST, which converts the JSON body using the declared parameter types;
    test_postgrest_accepts_decimal_settings covers that path.
    """
    tag = seeded["tag"]
    cur.execute(
        """
        SELECT public.insert_image_v3_0(
            species_common_name := %(species)s, experiment := %(experiment)s,
            wave_number := 1, germ_day := 3, germ_day_color := 'red', plant_age_days := 14,
            date_scanned_ := '2026-10-16', device_name := %(scanner)s,
            plant_qr_code := %(plant)s, accession_name := %(accession)s, frame_number_ := 1,
            phenotyper_name := 'Test', phenotyper_email := %(phenotyper_email)s,
            scientist_name := 'Test', scientist_email := %(scientist_email)s,
            num_frames := %(num_frames)s, exposure_time := %(exposure_time)s, gain := %(gain)s,
            brightness := %(brightness)s, contrast := %(contrast)s, gamma := %(gamma)s,
            seconds_per_rot := %(seconds_per_rot)s)
        """,
        {
            "species": seeded["species"],
            "scanner": seeded["scanner"],
            "experiment": f"exp-{tag}",
            "plant": f"{plant}-{tag}",
            "accession": f"acc-{tag}",
            "phenotyper_email": f"pheno-{tag}@example.org",
            "scientist_email": f"sci-{tag}@example.org",
            "exposure_time": exposure_time,
            "gain": gain,
            "brightness": brightness,
            "contrast": contrast,
            "gamma": gamma,
            "seconds_per_rot": seconds_per_rot,
            "num_frames": num_frames,
        },
    )
    image_id = cur.fetchone()[0]
    assert image_id is not None
    return image_id


def _settings_for(cur, image_id):
    cur.execute(
        f"""
        SELECT s.cyl_camera_settings_id, {", ".join("cs." + c for c in CAMERA_COLUMNS)}
          FROM cyl_images i
          JOIN cyl_scans s ON s.id = i.scan_id
          JOIN cyl_camera_settings cs ON cs.id = s.cyl_camera_settings_id
         WHERE i.id = %s
        """,
        (image_id,),
    )
    row = cur.fetchone()
    return row[0], dict(zip(CAMERA_COLUMNS, row[1:]))


def test_camera_setting_columns_are_double_precision(cur):
    cur.execute(
        """
        SELECT column_name, data_type FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'cyl_camera_settings'
           AND column_name = ANY(%s)
        """,
        (list(CAMERA_COLUMNS),),
    )
    assert dict(cur.fetchall()) == {c: "double precision" for c in CAMERA_COLUMNS}


def test_decimal_settings_are_stored_exactly(cur, seeded):
    image_id = _insert_image(
        cur,
        seeded,
        "plant-a",
        exposure_time=10000.5,
        gain=2.5,
        brightness=0.5,
        contrast=0.25,
        gamma=1.2,
        seconds_per_rot=7.5,
    )
    _, stored = _settings_for(cur, image_id)
    assert stored == {
        "scanner_exposure_time": 10000.5,
        "scanner_gain": 2.5,
        "scanner_brightness": 0.5,
        "scanner_contrast": 0.25,
        "scanner_gamma": 1.2,
        "scanner_seconds_per_rot": 7.5,
    }


def test_whole_number_settings_still_work(cur, seeded):
    image_id = _insert_image(
        cur,
        seeded,
        "plant-b",
        exposure_time=10000,
        gain=100,
        brightness=0,
        contrast=0,
        gamma=1,
        seconds_per_rot=7,
    )
    _, stored = _settings_for(cur, image_id)
    assert stored == {
        "scanner_exposure_time": 10000,
        "scanner_gain": 100,
        "scanner_brightness": 0,
        "scanner_contrast": 0,
        "scanner_gamma": 1,
        "scanner_seconds_per_rot": 7,
    }


def _num_frames(cur, settings_id):
    cur.execute("SELECT scanner_num_frames FROM cyl_camera_settings WHERE id = %s", (settings_id,))
    return cur.fetchone()[0]


def test_identical_settings_share_one_row(cur, seeded):
    first, _ = _settings_for(cur, _insert_image(cur, seeded, "plant-c", **SETTINGS))
    second, _ = _settings_for(cur, _insert_image(cur, seeded, "plant-d", **SETTINGS))
    assert first == second


def test_num_frames_is_stored(cur, seeded):
    settings_id, _ = _settings_for(
        cur, _insert_image(cur, seeded, "plant-e", num_frames=36, **SETTINGS)
    )
    assert _num_frames(cur, settings_id) == 36


def test_a_different_frame_count_is_a_different_settings_row(cur, seeded):
    first, _ = _settings_for(cur, _insert_image(cur, seeded, "plant-f", num_frames=72, **SETTINGS))
    second, _ = _settings_for(cur, _insert_image(cur, seeded, "plant-g", num_frames=36, **SETTINGS))
    assert first != second
    assert (_num_frames(cur, first), _num_frames(cur, second)) == (72, 36)


def test_a_missing_frame_count_matches_a_missing_frame_count(cur, seeded):
    first, _ = _settings_for(cur, _insert_image(cur, seeded, "plant-h", num_frames=None, **SETTINGS))
    second, _ = _settings_for(cur, _insert_image(cur, seeded, "plant-i", num_frames=None, **SETTINGS))
    assert first == second
    assert _num_frames(cur, first) is None


def test_an_upload_does_not_reuse_an_older_row_without_a_frame_count(cur, seeded):
    cur.execute(
        "INSERT INTO cyl_camera_settings (scanner_exposure_time, scanner_gain, scanner_brightness, "
        "scanner_contrast, scanner_gamma, scanner_seconds_per_rot) VALUES (9000, 150, 0.5, 0, 1.0, 7.5) "
        "RETURNING id"
    )
    older = cur.fetchone()[0]
    settings_id, _ = _settings_for(cur, _insert_image(cur, seeded, "plant-j", **SETTINGS))
    assert settings_id != older
    assert _num_frames(cur, settings_id) == 72


def test_a_negative_frame_count_is_refused(cur, seeded):
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_image(cur, seeded, "plant-k", num_frames=-1, **SETTINGS)


def test_only_one_insert_image_v3_0_exists(cur):
    # Two overloads with the same argument names make every PostgREST call fail with PGRST203.
    cur.execute(
        "SELECT count(*) FROM pg_proc "
        "WHERE proname = 'insert_image_v3_0' AND pronamespace = 'public'::regnamespace"
    )
    assert cur.fetchone()[0] == 1


def test_postgrest_accepts_decimal_settings(api, service_role_key):
    """A decimal setting reaches the function body through PostgREST.

    The species doesn't exist, so the function raises before writing anything. Before the fix,
    PostgREST rejected brightness 0.5 with 22P02 and never reached the function.
    """
    species = f"no-such-species-{uuid.uuid4().hex[:8]}"
    body = {
        "species_common_name": species,
        "experiment": "exp",
        "wave_number": 1,
        "germ_day": 3,
        "germ_day_color": "red",
        "plant_age_days": 14,
        "date_scanned_": "2026-10-16",
        "device_name": "scanner",
        "plant_qr_code": "plant",
        "accession_name": "acc",
        "frame_number_": 1,
        "phenotyper_name": "Test",
        "phenotyper_email": "pheno@example.org",
        "scientist_name": "Test",
        "scientist_email": "sci@example.org",
        "num_frames": 72,
        **SETTINGS,
    }
    try:
        status, resp = api(
            "/api/rest/v1/rpc/insert_image_v3_0",
            api_key=service_role_key,
            method="POST",
            data=body,
        )
    except (urllib.error.URLError, OSError) as e:
        if os.environ.get("CI"):
            pytest.fail(f"PostgREST gateway not reachable in CI: {e}")
        pytest.skip(f"PostgREST gateway not reachable ({e}); CI covers this")
    text = json.dumps(resp)
    assert status == 400, (status, resp)
    assert "22P02" not in text and "PGRST202" not in text and "PGRST203" not in text, (
        resp
    )
    assert f"Species {species} does not exist" in text, resp
