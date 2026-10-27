"""
Ties the web trait-export fixtures to #976's recipe functions (add-cyl-trait-csv-export,
tasks.md 1.2a; spec cyl-trait-export "Merged listing equals a single call").

The web export reads list_trait_recipes / get_trait_recipe_coverage / get_experiment_traits
in scan batches and merges the listings (design D2). Its tests run against a fake that
serves web/lib/cyl-trait-export/__fixtures__/golden/input.json verbatim, so this file
checks that input.json is what the real functions return, and that per-batch calls
merged the web's way equal one call over the whole selection.

Characterization: it pins #976's functions. Seeds with explicit ids base + fixture id and
rolls back. LOCAL ONLY against a migrated DB (CI's compose job runs it).
"""

import json

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.cyl_trait_export_fixture import (  # noqa: E402
    CHUNK_SIZES,
    EXPERIMENT,
    INPUT_JSON,
    SCANS,
    capture,
    new_base,
    seed,
)


@pytest.fixture
def seeded(pg_conn):
    base = new_base()
    with pg_conn.cursor() as cur:
        seed(cur, base)
        yield cur, base
    pg_conn.rollback()


@pytest.fixture(scope="module")
def recorded():
    doc = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
    doc.pop("_about")
    return doc


def merge_listings(chunks):
    """The web's merge (design D2 step 3), in Python."""
    merged = {}
    for chunk in chunks:
        for r in chunk:
            m = merged.setdefault(r["recipe_key"], dict(r, n_scans=0))
            m["n_scans"] += r["n_scans"]
            newest = r["newest_source_id"]
            if newest is not None and (
                m["newest_source_id"] is None or newest > m["newest_source_id"]
            ):
                m.update(
                    newest_source_id=newest,
                    recipe_key_version=r["recipe_key_version"],
                    recipe_kind=r["recipe_kind"],
                    definition=r["definition"],
                )
    rows = sorted(
        merged.values(),
        key=lambda r: (r["newest_source_id"] is None, -(r["newest_source_id"] or 0)),
    )
    return [dict(r, is_default=(i == 0)) for i, r in enumerate(rows)]


def test_recorded_fixture_matches_the_live_functions(seeded, recorded):
    cur, base = seeded
    assert capture(cur, base) == recorded


def test_recipe_keys_are_the_v1_keys(recorded):
    keys = recorded["keys"]
    assert keys["K"] != keys["K2"]
    assert all(len(keys[k]) == 64 for k in ("K", "K2"))
    by_id = {s["id"]: s["recipe_key"] for s in recorded["sources"]}
    # The superseded delivery (20) shares K's payload, so it shares K's key.
    assert by_id[20] == by_id[21] == by_id[22] == by_id[30] == keys["K"]
    assert by_id[40] == by_id[41] == keys["K2"]
    assert by_id[9] == "legacy:9"


def test_chunks_are_consecutive_slices_of_the_selection(recorded):
    s = sorted(SCANS)
    for size in CHUNK_SIZES:
        chunks = recorded["chunk_listings"][str(size)]
        assert [c["scan_ids"] for c in chunks] == [
            s[i : i + size] for i in range(0, len(s), size)
        ]


@pytest.mark.parametrize("size", CHUNK_SIZES)
def test_merged_chunk_listings_equal_one_call(recorded, size):
    whole = recorded["chunk_listings"][str(len(SCANS))][0]["rows"]
    chunks = [c["rows"] for c in recorded["chunk_listings"][str(size)]]
    assert merge_listings(chunks) == whole


@pytest.mark.parametrize("size", CHUNK_SIZES)
def test_per_chunk_coverage_and_traits_equal_one_call(seeded, recorded, size):
    cur, base = seeded
    exp = base + EXPERIMENT
    s_all = sorted(base + s for s in SCANS)
    cur.execute("SET LOCAL ROLE bloom_user")
    try:
        for label, rec in recorded["recipes"].items():
            key = f"legacy:{base + 9}" if label == "legacy:9" else rec["recipe_key"]
            coverage, traits = [], []
            for i in range(0, len(s_all), size):
                chunk = s_all[i : i + size]
                cur.execute(
                    "SELECT coalesce(json_agg(t), '[]'::json) FROM ("
                    " SELECT scan_id, status, source_id FROM"
                    " get_trait_recipe_coverage(%s, %s, %s)) t",
                    ([exp], chunk, key),
                )
                rows = cur.fetchone()[0]
                coverage += rows
                included = [r["scan_id"] for r in rows if r["status"] == "included"]
                if not included:
                    continue
                cur.execute(
                    "SELECT coalesce(json_agg(t), '[]'::json) FROM ("
                    " SELECT scan_id, trait_name, source_id, trait_value FROM"
                    " get_experiment_traits(%s, NULL, NULL, %s, %s)) t",
                    (exp, key, included),
                )
                traits += cur.fetchone()[0]

            def fx(v):
                return None if v is None else v - base

            got_cov = [
                (fx(r["scan_id"]), r["status"], fx(r["source_id"])) for r in coverage
            ]
            want_cov = [
                (r["scan_id"], r["status"], r["source_id"]) for r in rec["coverage"]
            ]
            assert got_cov == want_cov, label

            got_tr = sorted(
                (
                    fx(r["scan_id"]),
                    r["trait_name"],
                    fx(r["source_id"]),
                    json.dumps(r["trait_value"]),
                )
                for r in traits
            )
            want_tr = sorted(
                (
                    r["scan_id"],
                    r["trait_name"],
                    r["source_id"],
                    json.dumps(r["trait_value"]),
                )
                for r in rec["traits"]
            )
            assert got_tr == want_tr, label
    finally:
        cur.execute("RESET ROLE")


def test_selection_listings_have_no_k_in_wave_2(recorded):
    k = recorded["keys"]["K"]
    assert k not in {
        r["recipe_key"] for r in recorded["selection_listings"]["wave=2"]["rows"]
    }
    assert recorded["selection_listings"]["age=0"]["scan_ids"] == [9, 102, 200]
