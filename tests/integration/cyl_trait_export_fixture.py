"""The trait-export golden scenario (add-cyl-trait-csv-export, tasks 1.2 / 1.2a).

One experiment seeded with explicit ids ``base + fixture_id`` so the map between
fixture ids and seeded ids is exact and order-preserving. ``capture`` calls #976's
recipe RPCs as ``bloom_user`` and returns their rows in fixture id space; that is
what ``web/lib/cyl-trait-export/__fixtures__/golden/input.json`` records and what the
web tests' fake client serves.

Regenerate input.json (dev stack up, from the repo root):

    uv run --extra test python -m tests.integration.cyl_trait_export_fixture

The integration test re-seeds, re-captures and asserts equality with the file, so the
web fixtures cannot drift from the SQL. Every value is float4-representable, because
``cyl_scan_traits.value`` is ``real``.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INPUT_JSON = (
    REPO_ROOT
    / "web"
    / "lib"
    / "cyl-trait-export"
    / "__fixtures__"
    / "golden"
    / "input.json"
)

EXPERIMENT = 1
SPECIES = 1
WAVES = {1: {"number": 1, "name": "Wave 1"}, 2: {"number": 2, "name": None}}
ACCESSIONS = {1: "FIXTURE-ACC-A", 2: "FIXTURE-ACC-B", 3: "FIXTURE-ACC-C"}
PLANTS = {
    # plant: (wave, accession, qr_code, germ_day, germ_day_color)
    1: (1, 1, "FX-P1", 3, "Purple"),
    2: (1, 2, "FX-P2", 3, None),
    3: (1, 3, "FX-P3", 4, None),
    4: (1, 1, "FX-P4", 4, None),
    5: (1, 2, "FX-P5", 3, None),
    6: (2, 3, "FX-P6", 2, None),
    7: (2, 1, "FX-P7", 2, None),
}
SCANS = {
    # scan: (plant, plant_age_days, date_scanned, uploaded_at)
    9: (1, 0, "2026-03-02", "2026-03-02T17:00:00+00:00"),
    10: (1, 7, "2026-03-09", "2026-03-09T17:00:00+00:00"),
    100: (2, 7, "2026-03-09", None),
    101: (3, 7, "2026-03-09", None),
    102: (4, 0, "2026-03-02", None),
    103: (5, 7, "2026-03-09", None),
    200: (6, 0, "2026-04-01", None),
    201: (7, 2, "2026-04-03", None),
}


def _pipeline_meta(traits_code_sha: str, sleap_nn: str, contract: str) -> dict:
    return {
        "predict_models": [
            {
                "registry_id": "lateral-root",
                "version": "v3",
                "weights_checksum": "sha256:lat3",
                "root_type": "lateral",
                "sleap_nn_version": sleap_nn,
            },
            {
                "registry_id": "primary-root",
                "version": "v2",
                "weights_checksum": "sha256:pri2",
                "root_type": "primary",
                "sleap_nn_version": sleap_nn,
            },
        ],
        "predict_code_sha": "abc1234",
        "traits_code_sha": traits_code_sha,
        "contract_version": contract,
        "traits_sleap_roots_version": "0.2.1",
        "predict_container_digest": "sha256:predict1",
        "traits_container_digest": "sha256:traits1",
        "predict_inference_config": {"device": "cuda", "batch_size": 4},
    }


# source: (scan or None, name, metadata or None). metadata None -> legacy:<id>.
# Source 20 is an older delivery of recipe K to scan 100 (same payload, so same key);
# only its observed-only fields differ. Source 30 supersedes it.
SOURCES = {
    9: (None, "legacy-fixture-9", None),
    20: (100, "fixture-k-old", _pipeline_meta("def4567", "0.0.9", "0.1.0a8")),
    21: (9, "fixture-k-9", _pipeline_meta("def4567", "0.1.0", "0.1.0a9")),
    22: (10, "fixture-k-10", _pipeline_meta("def4567", "0.1.0", "0.1.0a9")),
    30: (100, "fixture-k-100", _pipeline_meta("def4567", "0.1.0", "0.1.0a9")),
    40: (101, "fixture-k2-101", _pipeline_meta("def7890", "0.1.0", "0.1.0a9")),
    41: (201, "fixture-k2-201", _pipeline_meta("def7890", "0.1.0", "0.1.0a9")),
}

F4_MAX = 3.4028234663852886e38
NAN, INF = float("nan"), float("inf")

# (scan, source or None, trait, value). value None = SQL NULL.
TRAIT_ROWS = [
    (9, 21, "lateral_count_min", None),
    (9, 21, "lateral_length_mean", NAN),
    (9, 21, "curve_index_median", INF),
    (9, 21, "primary_length", -INF),
    (9, 21, "Ångström_width", -0.0),
    (9, 21, "𝛼_angle", 0.1),
    (9, 21, "Zeta_depth", F4_MAX),
    (10, 22, "lateral_count_min", 4.0),
    (10, 22, "lateral_length_mean", 12.5),
    (10, 22, "curve_index_median", 0.75),
    (10, 22, "primary_length", 85.25),
    (10, 22, "Ångström_width", 1.5),
    (10, 22, "𝛼_angle", 45.0),
    (10, 22, "Zeta_depth", -2.5),
    (100, 20, "lateral_count_min", 99.0),
    (100, 20, "lateral_extra_old", 5.0),
    (100, 30, "lateral_count_min", 7.0),
    (100, 30, "lateral_length_mean", 10.125),
    (100, 30, "primary_length", 90.0),
    (100, 30, "Ångström_width", 2.0),
    (100, 30, "𝛼_angle", 30.5),
    (100, 30, "Zeta_depth", 1e-7),
    (101, 40, "lateral_count_min", 3.0),
    (101, 40, "primary_length", 70.0),
    (102, 9, "crown_count_min", 6.0),
    (102, 9, "crown_length_mean", 20.5),
    (103, None, "primary_length", 55.0),
    (201, 41, "lateral_count_min", 5.0),
    (201, 41, "primary_length", 60.0),
]

CHUNK_SIZES = (1, 2, len(SCANS))
SELECTIONS = {
    "wave=2": {"wave_number": 2},
    "age=0": {"plant_age_days": 0},
}


def new_base() -> int:
    """A random id base far from real ids; every seeded id is base + fixture id."""
    return random.randrange(10**12, 9 * 10**12, 10**6)


def seed(cur, base: int) -> None:
    """Insert the scenario. The caller owns the transaction (roll back afterwards)."""
    b = base
    cur.execute(
        "INSERT INTO species (id, common_name, genus, species) VALUES (%s, %s, %s, %s)",
        # (genus, species) is UNIQUE, so the names are fixture-only.
        (b + SPECIES, "fixture-plant", "Fixturia", "exportii"),
    )
    cur.execute(
        "INSERT INTO cyl_experiments (id, name, species_id) VALUES (%s, %s, %s)",
        (b + EXPERIMENT, "Fixture Diversity Screen", b + SPECIES),
    )
    for w, v in WAVES.items():
        cur.execute(
            "INSERT INTO cyl_waves (id, experiment_id, number, name) VALUES (%s, %s, %s, %s)",
            (b + w, b + EXPERIMENT, v["number"], v["name"]),
        )
    for a, name in ACCESSIONS.items():
        cur.execute("INSERT INTO accessions (id, name) VALUES (%s, %s)", (b + a, name))
    for p, (w, a, qr, germ, color) in PLANTS.items():
        cur.execute(
            "INSERT INTO cyl_plants (id, wave_id, accession_id, qr_code, germ_day, germ_day_color)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (b + p, b + w, b + a, qr, germ, color),
        )
    for s, (p, age, date, uploaded) in SCANS.items():
        cur.execute(
            "INSERT INTO cyl_scans (id, plant_id, plant_age_days, date_scanned, uploaded_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            (b + s, b + p, age, date, uploaded),
        )
    for sid, (scan, name, meta) in SOURCES.items():
        meta_json = None if meta is None else json.dumps(meta)
        cur.execute(
            "INSERT INTO cyl_trait_sources (id, name, metadata, recipe_key, recipe_key_version, scan_id)"
            " VALUES (%s, %s, %s::jsonb,"
            "  CASE WHEN %s::jsonb IS NULL THEN 'legacy:' || %s"
            "       ELSE public.cyl_trait_recipe_key_v1(%s::jsonb) END,"
            "  CASE WHEN %s::jsonb IS NULL THEN NULL ELSE 1 END, %s)",
            (
                b + sid,
                name,
                meta_json,
                meta_json,
                b + sid,
                meta_json,
                meta_json,
                None if scan is None else b + scan,
            ),
        )
    trait_ids: dict[str, int] = {}
    for _, _, trait, _ in TRAIT_ROWS:
        if trait not in trait_ids:
            cur.execute(
                "INSERT INTO cyl_traits (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
                (trait,),
            )
            cur.execute("SELECT id FROM cyl_traits WHERE name = %s", (trait,))
            trait_ids[trait] = cur.fetchone()[0]
    for scan, source, trait, value in TRAIT_ROWS:
        cur.execute(
            "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value)"
            " VALUES (%s, %s, %s, %s::real)",
            (
                b + scan,
                None if source is None else b + source,
                trait_ids[trait],
                None if value is None else _pg_float(value),
            ),
        )


def _pg_float(v: float) -> str:
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "Infinity" if v > 0 else "-Infinity"
    return repr(v)


# --------------------------------------------------------------------------- #
# Capture: RPC rows as PostgreSQL renders them in JSON, mapped to fixture ids.
# --------------------------------------------------------------------------- #


class IdMap:
    def __init__(self, base: int):
        self.base = base

    def id(self, v):
        return None if v is None else v - self.base

    def key(self, k):
        if k is not None and k.startswith("legacy:"):
            return f"legacy:{int(k.split(':', 1)[1]) - self.base}"
        return k

    def keys(self, ks):
        return None if ks is None else [self.key(k) for k in ks]


def _json_rows(cur, sql: str, params) -> list[dict]:
    """Rows as PostgreSQL's own JSON rendering (what PostgREST sends)."""
    cur.execute(f"SELECT coalesce(json_agg(t), '[]'::json) FROM ({sql}) t", params)
    return cur.fetchone()[0]


def _map_listing(rows, m: IdMap):
    out = []
    for r in rows:
        r = dict(r)
        r["recipe_key"] = m.key(r["recipe_key"])
        r["newest_source_id"] = m.id(r["newest_source_id"])
        d = r.get("definition")
        if isinstance(d, dict) and "source_id" in d:
            d = dict(d, source_id=m.id(d["source_id"]))
            r["definition"] = d
        out.append(r)
    return out


def _listing(cur, m, exp, scan_ids):
    rows = _json_rows(
        cur,
        "SELECT recipe_key, recipe_key_version, recipe_kind, definition, n_scans,"
        " newest_source_id, is_default FROM list_trait_recipes(%s, %s)",
        ([exp], scan_ids),
    )
    return _map_listing(rows, m)


def _coverage(cur, m, exp, scan_ids, key):
    rows = _json_rows(
        cur,
        "SELECT scan_id, experiment_id, plant_qr_code, recipe_key, status, source_id,"
        " available_recipes FROM get_trait_recipe_coverage(%s, %s, %s)",
        ([exp], scan_ids, key),
    )
    return [
        dict(
            r,
            scan_id=m.id(r["scan_id"]),
            experiment_id=m.id(r["experiment_id"]),
            recipe_key=m.key(r["recipe_key"]),
            source_id=m.id(r["source_id"]),
            available_recipes=m.keys(r["available_recipes"]),
        )
        for r in rows
    ]


def _traits(cur, m, exp, scan_ids, key):
    rows = _json_rows(
        cur,
        "SELECT scan_id, trait_name, source_id, trait_value"
        " FROM get_experiment_traits(%s, NULL, NULL, %s, %s)",
        (exp, key, scan_ids),
    )
    rows = [
        dict(r, scan_id=m.id(r["scan_id"]), source_id=m.id(r["source_id"]))
        for r in rows
    ]
    return sorted(rows, key=lambda r: (r["scan_id"], r["trait_name"]))


def capture(cur, base: int, *, role: str = "bloom_user") -> dict:
    """Call the RPCs as ``role`` over the seeded scenario and return fixture-space rows."""
    m = IdMap(base)
    exp = base + EXPERIMENT
    s_all = sorted(base + s for s in SCANS)

    cur.execute(
        "SELECT recipe_key, id FROM cyl_trait_sources WHERE id = ANY(%s)",
        ([base + s for s in SOURCES],),
    )
    by_source = {m.id(i): m.key(k) for k, i in cur.fetchall()}
    keys = {
        "K": by_source[30],
        "K2": by_source[40],
        "legacy:9": "legacy:9",
        "unattributed": "unattributed",
    }

    cur.execute(
        "SELECT coalesce(json_agg(t ORDER BY t.scan_id), '[]'::json) FROM"
        " (SELECT * FROM cyl_scans_extended WHERE experiment_id = %s) t",
        (exp,),
    )
    ext = cur.fetchone()[0]
    for r in ext:
        for col in (
            "scan_id",
            "plant_id",
            "accession_id",
            "wave_id",
            "experiment_id",
            "species_id",
        ):
            r[col] = m.id(r[col])

    cur.execute(f"SET LOCAL ROLE {role}")
    try:
        chunks = {}
        for size in CHUNK_SIZES:
            chunks[str(size)] = [
                {
                    "scan_ids": [m.id(i) for i in s_all[i : i + size]],
                    "rows": _listing(cur, m, exp, s_all[i : i + size]),
                }
                for i in range(0, len(s_all), size)
            ]
        selections = {}
        for name, filt in SELECTIONS.items():
            ids = sorted(
                base + s
                for s, (p, age, _, _) in SCANS.items()
                if all(
                    (WAVES[PLANTS[p][0]]["number"] if k == "wave_number" else age) == v
                    for k, v in filt.items()
                )
            )
            selections[name] = {
                "filters": filt,
                "scan_ids": [m.id(i) for i in ids],
                "rows": _listing(cur, m, exp, ids),
            }
        per_key = {}
        for label, k in keys.items():
            real_key = f"legacy:{base + 9}" if label == "legacy:9" else k
            per_key[label] = {
                "recipe_key": k,
                "coverage": _coverage(cur, m, exp, s_all, real_key),
                "traits": _traits(cur, m, exp, s_all, real_key),
            }
    finally:
        cur.execute("RESET ROLE")

    return {
        "experiment": {"id": EXPERIMENT, "name": "Fixture Diversity Screen"},
        "scans_extended": ext,
        "genotypes": {str(a): name for a, name in ACCESSIONS.items()},
        "sources": [
            {
                "id": sid,
                "name": name,
                "scan_id": scan,
                "recipe_key": by_source[sid],
                "recipe_key_version": None if meta is None else 1,
                "metadata": meta,
            }
            for sid, (scan, name, meta) in sorted(SOURCES.items())
        ],
        "keys": keys,
        "chunk_listings": chunks,
        "selection_listings": selections,
        "recipes": per_key,
    }


def main() -> None:
    import psycopg

    from tests.integration.conftest import (
        POSTGRES_DB,
        POSTGRES_HOST_PORT,
        POSTGRES_PASSWORD,
        POSTGRES_USER,
    )

    base = new_base()
    conninfo = (
        f"host=127.0.0.1 port={POSTGRES_HOST_PORT} dbname={POSTGRES_DB}"
        f" user={POSTGRES_USER} password={POSTGRES_PASSWORD}"
    )
    with psycopg.connect(conninfo) as conn:
        with conn.cursor() as cur:
            seed(cur, base)
            recorded = capture(cur, base)
        conn.rollback()
    doc = {
        "_about": (
            "Recorded by tests/integration/cyl_trait_export_fixture.py from #976's RPCs on a"
            " migrated DB, as bloom_user, in fixture id space. Do not edit by hand; the"
            " integration test checks it against the live functions."
        ),
        **recorded,
    }
    INPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    INPUT_JSON.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {INPUT_JSON.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
