"""What a migration's SQL creates, changes and drops.

A small tokenizer that understands quotes, dollar-quotes and comments, so names are read
from real statements only: `--` inside a string is not a comment, and DDL inside a
function body is not the migration's DDL. `DO` bodies are scanned, because that is where
guarded constraint additions live. It extracts names; it does not enforce rules.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_IDENT = r'(?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*)'
_QNAME = rf"{_IDENT}(?:\s*\.\s*{_IDENT})?"
_QNAME_LIST = rf"{_QNAME}(?:\s*,\s*{_QNAME})*"
_DOLLAR_TAG = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")

_CREATE_TABLE = re.compile(
    rf"\bCREATE\s+(?:(?:GLOBAL|LOCAL)\s+)?(?:(?:TEMP|TEMPORARY|UNLOGGED)\s+)?TABLE\s+"
    rf"(?:IF\s+NOT\s+EXISTS\s+)?({_QNAME})",
    re.I,
)
_ALTER_TABLE = re.compile(
    rf"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?({_QNAME})\s*(.*)$", re.I | re.S
)
_CREATE_INDEX = re.compile(
    rf"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
    rf"({_QNAME})\s+ON\b",
    re.I,
)
_DROP_INDEX = re.compile(
    rf"\bDROP\s+INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+EXISTS\s+)?({_QNAME_LIST})", re.I
)
_DROP_TABLE = re.compile(rf"\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?({_QNAME_LIST})", re.I)
_CREATE_VIEW = re.compile(
    rf"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:TEMP|TEMPORARY)\s+)?(?:RECURSIVE\s+)?"
    rf"(?:MATERIALIZED\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?({_QNAME})",
    re.I,
)
_DROP_VIEW = re.compile(
    rf"\bDROP\s+(?:MATERIALIZED\s+)?VIEW\s+(?:IF\s+EXISTS\s+)?({_QNAME_LIST})", re.I
)
_INLINE_CONSTRAINT = re.compile(rf"\bCONSTRAINT\s+({_IDENT})", re.I)

_ADD_CONSTRAINT = re.compile(rf"^ADD\s+CONSTRAINT\s+({_IDENT})", re.I)
_ADD_UNNAMED = re.compile(r"^ADD\s+(?:CHECK|UNIQUE|PRIMARY\s+KEY|FOREIGN\s+KEY|EXCLUDE)\b", re.I)
_DROP_CONSTRAINT = re.compile(rf"^DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?({_IDENT})", re.I)
_RENAME_TO = re.compile(rf"^RENAME\s+TO\s+({_IDENT})", re.I)
_STRUCTURAL_ACTION = re.compile(r"^(?:ADD|DROP|ALTER|RENAME)\b", re.I)


@dataclass(frozen=True)
class MigrationFacts:
    tables_created: frozenset[str] = frozenset()
    tables_altered: frozenset[str] = frozenset()
    tables_dropped: frozenset[str] = frozenset()
    tables_renamed: frozenset[tuple[str, str]] = frozenset()
    constraints_added: frozenset[str] = frozenset()
    constraints_dropped: frozenset[str] = frozenset()
    indexes_added: frozenset[str] = frozenset()
    indexes_dropped: frozenset[str] = frozenset()
    views_created: frozenset[str] = frozenset()
    views_dropped: frozenset[str] = frozenset()
    unnamed_constraints: tuple[str, ...] = ()

    @property
    def tables_touched(self) -> frozenset[str]:
        """What a diagram of this change must show: tables created, altered or renamed to, and views created."""
        return (
            self.tables_created
            | self.tables_altered
            | {new for _, new in self.tables_renamed}
            | self.views_created
        )

    @property
    def changes_schema(self) -> bool:
        return any(
            (
                self.tables_created, self.tables_altered, self.tables_dropped,
                self.tables_renamed, self.constraints_added, self.constraints_dropped,
                self.indexes_added, self.indexes_dropped, self.views_created,
                self.views_dropped, self.unnamed_constraints,
            )
        )

    def __or__(self, other: MigrationFacts) -> MigrationFacts:
        return MigrationFacts(
            tables_created=self.tables_created | other.tables_created,
            tables_altered=self.tables_altered | other.tables_altered,
            tables_dropped=self.tables_dropped | other.tables_dropped,
            tables_renamed=self.tables_renamed | other.tables_renamed,
            constraints_added=self.constraints_added | other.constraints_added,
            constraints_dropped=self.constraints_dropped | other.constraints_dropped,
            indexes_added=self.indexes_added | other.indexes_added,
            indexes_dropped=self.indexes_dropped | other.indexes_dropped,
            views_created=self.views_created | other.views_created,
            views_dropped=self.views_dropped | other.views_dropped,
            unnamed_constraints=tuple(
                dict.fromkeys(self.unnamed_constraints + other.unnamed_constraints)
            ),
        )


@dataclass
class _Statement:
    code: str  # comments removed, string literals blanked, dollar bodies replaced by $$
    bodies: list[str]  # the dollar-quoted bodies, raw, in order


def _is_ident_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _split(sql: str) -> list[_Statement]:
    statements: list[_Statement] = []
    code: list[str] = []
    bodies: list[str] = []

    def flush() -> None:
        text = " ".join("".join(code).split())
        if text:
            statements.append(_Statement(text, list(bodies)))
        code.clear()
        bodies.clear()

    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end == -1 else end
            code.append(" ")
        elif sql.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if sql.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            code.append(" ")
        elif ch == "'":
            e_string = i > 0 and sql[i - 1] in "eE" and (i < 2 or not _is_ident_char(sql[i - 2]))
            i += 1
            while i < n:
                if e_string and sql[i] == "\\":
                    i += 2
                elif sql[i] == "'":
                    if sql.startswith("''", i):
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
            code.append("''")
        elif ch == '"':
            end = i + 1
            while end < n:
                if sql.startswith('""', end):
                    end += 2
                elif sql[end] == '"':
                    break
                else:
                    end += 1
            code.append(sql[i : end + 1])
            i = end + 1
        elif ch == "$" and not (i > 0 and _is_ident_char(sql[i - 1])) and (m := _DOLLAR_TAG.match(sql, i)):
            tag = m.group(0)
            end = sql.find(tag, m.end())
            end = n if end == -1 else end
            bodies.append(sql[m.end() : end])
            code.append(" $$ ")
            i = end + len(tag)
        elif ch == ";":
            flush()
            i += 1
        else:
            code.append(ch)
            i += 1
    flush()
    return statements


def _unquote(part: str) -> str:
    if part.startswith('"'):
        return part[1:-1].replace('""', '"')
    return part.lower()


def _table(raw: str) -> str:
    parts = [_unquote(p) for p in re.findall(_IDENT, raw)]
    if len(parts) == 2 and parts[0] == "public":
        return parts[1]
    return ".".join(parts)


def _last(raw: str) -> str:
    return _unquote(re.findall(_IDENT, raw)[-1])


def _split_names(raw: str) -> list[str]:
    return [name for name in (s.strip() for s in raw.split(",")) if name]


def _actions(text: str) -> list[str]:
    """ALTER TABLE actions, split on commas outside parentheses."""
    actions, depth, start = [], 0, 0
    for idx, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            actions.append(text[start:idx].strip())
            start = idx + 1
    actions.append(text[start:].strip())
    return [a for a in actions if a]


class _Collector:
    def __init__(self) -> None:
        self.created: set[str] = set()
        self.altered: set[str] = set()
        self.dropped: set[str] = set()
        self.renamed: set[tuple[str, str]] = set()
        self.c_added: set[str] = set()
        self.c_dropped: set[str] = set()
        self.i_added: set[str] = set()
        self.i_dropped: set[str] = set()
        self.v_created: set[str] = set()
        self.v_dropped: set[str] = set()
        self.unnamed: list[str] = []

    def statement(self, code: str) -> None:
        candidates = [
            (m.start(), kind, m)
            for kind, pattern in (
                ("create_table", _CREATE_TABLE),
                ("alter_table", _ALTER_TABLE),
                ("create_index", _CREATE_INDEX),
                ("drop_index", _DROP_INDEX),
                ("drop_table", _DROP_TABLE),
                ("create_view", _CREATE_VIEW),
                ("drop_view", _DROP_VIEW),
            )
            if (m := pattern.search(code))
        ]
        if not candidates:
            return
        _, kind, m = min(candidates, key=lambda c: c[0])
        getattr(self, "_" + kind)(m, code)

    def _create_table(self, m: re.Match, code: str) -> None:
        self.created.add(_table(m.group(1)))
        for c in _INLINE_CONSTRAINT.finditer(code, m.end()):
            self.c_added.add(_unquote(c.group(1)))

    def _alter_table(self, m: re.Match, code: str) -> None:
        table = _table(m.group(1))
        for action in _actions(m.group(2)):
            if rename := _RENAME_TO.match(action):
                self.renamed.add((table, _unquote(rename.group(1))))
                continue
            if added := _ADD_CONSTRAINT.match(action):
                self.c_added.add(_unquote(added.group(1)))
            elif _ADD_UNNAMED.match(action):
                self.unnamed.append(table)
            elif dropped := _DROP_CONSTRAINT.match(action):
                self.c_dropped.add(_unquote(dropped.group(1)))
            if _STRUCTURAL_ACTION.match(action):
                self.altered.add(table)

    def _create_index(self, m: re.Match, code: str) -> None:
        self.i_added.add(_last(m.group(1)))

    def _drop_index(self, m: re.Match, code: str) -> None:
        self.i_dropped.update(_last(name) for name in _split_names(m.group(1)))

    def _drop_table(self, m: re.Match, code: str) -> None:
        self.dropped.update(_table(name) for name in _split_names(m.group(1)))

    def _create_view(self, m: re.Match, code: str) -> None:
        self.v_created.add(_table(m.group(1)))

    def _drop_view(self, m: re.Match, code: str) -> None:
        self.v_dropped.update(_table(name) for name in _split_names(m.group(1)))

    def facts(self) -> MigrationFacts:
        return MigrationFacts(
            tables_created=frozenset(self.created),
            tables_altered=frozenset(self.altered),
            tables_dropped=frozenset(self.dropped),
            tables_renamed=frozenset(self.renamed),
            constraints_added=frozenset(self.c_added),
            constraints_dropped=frozenset(self.c_dropped),
            indexes_added=frozenset(self.i_added),
            indexes_dropped=frozenset(self.i_dropped),
            views_created=frozenset(self.v_created),
            views_dropped=frozenset(self.v_dropped),
            unnamed_constraints=tuple(dict.fromkeys(self.unnamed)),
        )


def _collect(sql: str, collector: _Collector) -> None:
    for stmt in _split(sql):
        if re.match(r"DO\b", stmt.code, re.I):
            for body in stmt.bodies:
                _collect(body, collector)
        else:
            collector.statement(stmt.code)


def scan(sql: str) -> MigrationFacts:
    """Facts about one migration's SQL text."""
    collector = _Collector()
    _collect(sql, collector)
    return collector.facts()


def scan_files(paths: Iterable[Path]) -> MigrationFacts:
    """The union of scan() over several migration files."""
    facts = MigrationFacts()
    for path in paths:
        facts = facts | scan(Path(path).read_text(encoding="utf-8"))
    return facts
