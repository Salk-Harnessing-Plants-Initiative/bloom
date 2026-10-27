"""An in-memory stand-in for the parts of the supabase client the scrna commands use.

PostgREST select/insert/update with eq, is_, in_, order, limit and range, and Storage upload.
Any request can be made to fail, before or after it commits.
"""

from __future__ import annotations

import itertools
import threading


class Response:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, client, table):
        self.client, self.table = client, table
        self.op, self.payload, self.columns = "select", None, "*"
        self.filters, self.order_by, self.window = [], None, None
        self.returning, self.retry_enabled = "representation", True

    def select(self, *columns, count=None):
        self.op, self.columns = "select", ",".join(columns) or "*"
        return self

    def insert(self, rows, *, returning="representation", **_):
        self.op, self.returning = "insert", str(returning)
        self.payload = rows if isinstance(rows, list) else [rows]
        return self

    def update(self, values, *, returning="representation", **_):
        self.op, self.payload, self.returning = "update", values, str(returning)
        return self

    def eq(self, column, value):
        self.filters.append(lambda r: r.get(column) == value)
        return self

    def is_(self, column, value):
        wanted = None if str(value).lower() == "null" else value
        self.filters.append(lambda r: r.get(column) is wanted)
        return self

    def in_(self, column, values):
        self.client.asked.append(list(values))
        allowed = set(values)
        self.filters.append(lambda r: r.get(column) in allowed)
        return self

    def order(self, column, *, desc=False, **_):
        self.order_by = (column, desc)
        return self

    def limit(self, size, **_):
        self.window = (0, size - 1)
        return self

    def range(self, start, end):
        self.window = (start, end)
        return self

    def retry(self, enabled):
        self.retry_enabled = enabled
        return self

    def execute(self):
        return self.client._execute(self)


class Bucket:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def upload(self, path, file, file_options=None):
        fault = self.client._take_fault("upload", self.name, path)
        if fault and not fault["commit"]:
            raise fault["error"]
        self.client.objects[(self.name, path)] = (bytes(file), dict(file_options or {}))
        self.client.log.append(("upload", self.name, path, None))
        if fault:
            raise fault["error"]
        return {"path": path}


class Session:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class Storage:
    def __init__(self, client):
        self.client = client
        self.session = Session()

    def from_(self, bucket):
        return Bucket(self.client, bucket)


class Options:
    def __init__(self):
        self.headers = {"apikey": "anon", "Authorization": "Bearer first-login"}
        self.storage_client_timeout = 20


# A gene file upload is sent once and repeated after each REPEAT_DELAYS_S wait, so a fault
# that should stop the load has to last that many tries.
UPLOAD_TRIES = 4


def fail_one_upload(client, error, *, commit=False):
    """Fail every try of one gene's upload (the first one sent), so the load stops whatever
    order the parallel uploads run in."""
    first = []

    def when(op, table, payload):
        if op != "upload":
            return False
        if not first:
            first.append(payload)
        return payload == first[0]

    client.fail(when, error, commit=commit, times=UPLOAD_TRIES)


class FakeClient:
    """Tables are lists of dicts; inserted rows get an increasing `id`."""

    def __init__(self, tables=None):
        self.tables = {name: [dict(r) for r in rows] for name, rows in (tables or {}).items()}
        self.objects: dict = {}
        self.log: list = []
        self.faults: list = []
        self._ids = itertools.count(1000)
        self.asked: list[list] = []  # every in_ filter's values, to size the URL
        self.storage = Storage(self)
        self.options = Options()
        self.storage_url = "http://fake/storage/v1/"
        # Each storage handle opened as the real client's _init_storage_client would, with the
        # thread that asked for it and the login it carries.
        self.opened: list[tuple[int, str, Storage]] = []

    def _init_storage_client(self, *, storage_url, headers, storage_client_timeout):
        storage = Storage(self)
        self.opened.append((threading.get_ident(), headers.get("Authorization"), storage))
        return storage

    def table(self, name):
        return Query(self, name)

    def fail(self, when, error, *, commit=False, times=1):
        """Raise `error` on the first `times` requests where when(op, table, payload) holds.

        With `commit`, the request takes effect first, the way a write can commit on
        the server after the client has given up on it.
        """
        self.faults.append({"when": when, "error": error, "commit": commit, "left": times})

    def _take_fault(self, op, table, payload):
        for fault in self.faults:
            if fault["when"](op, table, payload):
                fault["left"] -= 1
                if fault["left"] <= 0:
                    self.faults.remove(fault)
                return fault
        return None

    def _execute(self, q):
        rows = self.tables.setdefault(q.table, [])
        fault = self._take_fault(q.op, q.table, q.payload)
        if fault and not fault["commit"]:
            raise fault["error"]
        result = self._apply(q, rows)
        self.log.append((q.op, q.table, len(result) if q.op == "select" else
                         (len(q.payload) if q.op == "insert" else None), q.retry_enabled))
        if fault:
            raise fault["error"]
        return Response(result)

    def _apply(self, q, rows):
        if q.op == "insert":
            out = []
            for row in q.payload:
                row = dict(row)
                row.setdefault("id", next(self._ids))
                rows.append(row)
                out.append(dict(row))
            return out if q.returning == "representation" else []
        matched = [r for r in rows if all(f(r) for f in q.filters)]
        if q.op == "update":
            for r in matched:
                r.update(q.payload)
            return [dict(r) for r in matched] if q.returning == "representation" else []
        if q.order_by:
            column, desc = q.order_by
            matched = sorted(matched, key=lambda r: (r.get(column) is None, r.get(column)),
                             reverse=desc)
        if q.window:
            matched = matched[q.window[0]: q.window[1] + 1]
        if q.columns == "*":
            return [dict(r) for r in matched]
        columns = [c.strip() for c in q.columns.split(",")]
        return [{c: r.get(c) for c in columns} for r in matched]


SPECIES = [{"id": 1, "common_name": "Arabidopsis"}]


def database(datasets=()) -> FakeClient:
    """A database holding these datasets and one species."""
    return FakeClient({
        "scrna_datasets": [dict({"deleted_at": None, "metadata": {}}, **d) for d in datasets],
        "species": SPECIES,
    })
