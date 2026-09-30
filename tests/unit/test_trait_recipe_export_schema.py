"""Checks on export sidecar v1 (add-cyl-trait-recipe-key; cyl-trait-read "Recipe read
RPCs supply every export sidecar field").

The root test environment has no jsonschema package, so this does not validate the
example against the schema; it checks what the spec requires of the files instead:
the schema parses, the example carries every required property at every level, and
every schema property is mapped in the docs page's field table to an RPC column, a
cyl_trait_sources.metadata key path, "exporter-supplied", or (for a container) "object" or
"array".
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WIKI = REPO_ROOT / "_WIKI" / "SUPABASE"
SCHEMA = WIKI / "trait-recipes.export.schema.json"
EXAMPLE = WIKI / "trait-recipes.export.example.json"
PAGE = WIKI / "trait-recipes.md"
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"
READ_FUNCTIONS = (
    "get_experiment_traits",
    "list_trait_recipes",
    "get_trait_recipe_coverage",
)


def _schema():
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def _properties(node, prefix=""):
    """Every property path in the schema: `a.b`, with `[]` for array items."""
    paths = []
    for name, sub in node.get("properties", {}).items():
        path = f"{prefix}{name}"
        paths.append(path)
        paths += _properties(sub, path + ".")
        if sub.get("type") == "array" and isinstance(sub.get("items"), dict):
            paths += _properties(sub["items"], path + "[].")
    return paths


def _missing_required(schema, value, path="$"):
    missing = []
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                missing.append(f"{path}.{name}")
        for name, sub in schema.get("properties", {}).items():
            if name in value:
                missing += _missing_required(sub, value[name], f"{path}.{name}")
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            missing += _missing_required(schema["items"], item, f"{path}[{i}]")
    return missing


def _field_table():
    """{field: source} from the page's `| Field | Source |` table."""
    text = PAGE.read_text(encoding="utf-8")
    start = re.search(r"^\|\s*Field\s*\|\s*Source\s*\|", text, re.M).start()
    rows = {}
    for line in text[start:].splitlines()[2:]:
        if not line.startswith("|"):
            break
        cells = [c.strip().strip("`") for c in line.strip("|").split("|")]
        rows[cells[0]] = cells[1]
    return rows


def _rpc_columns():
    """{function: {columns}} from the recipe-read migration's RETURNS TABLE lists."""
    m3 = sorted(MIGRATIONS.glob("*_add_cyl_trait_recipe_reads.sql"))[-1]
    text = m3.read_text(encoding="utf-8")
    out = {}
    for fn in READ_FUNCTIONS:
        m = re.search(
            rf"FUNCTION\s+public\.{fn}\(.*?RETURNS\s+TABLE\s*\((.*?)\)\s*\n\s*LANGUAGE",
            text,
            re.S | re.I,
        )
        assert m, fn
        out[fn] = {line.split()[0] for line in m.group(1).splitlines() if line.strip()}
    return out


def test_schema_parses_as_draft_2020_12():
    schema = _schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["export_schema_version"]["const"] == 1


def test_example_has_every_required_property():
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert _missing_required(_schema(), example) == []


def test_every_property_is_in_the_field_table():
    table = _field_table()
    assert sorted(set(_properties(_schema())) - set(table)) == []
    assert sorted(set(table) - set(_properties(_schema()))) == []


def test_every_source_is_real():
    columns = _rpc_columns()
    bad = []
    for field, source in _field_table().items():
        if source in ("exporter-supplied", "object", "array") or source.startswith(
            "cyl_trait_sources.metadata->"
        ):
            continue
        fn, _, col = source.partition(".")
        if fn not in columns or col not in columns[fn]:
            bad.append((field, source))
    assert bad == []
