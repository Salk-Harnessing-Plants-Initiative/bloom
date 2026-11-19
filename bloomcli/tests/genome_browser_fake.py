"""Bloom's genome tables and browser-build functions, kept in memory for `genome index` tests.

Each function makes the checks the browser-build migrations make, with the same messages and
error codes, so a request real Bloom would refuse is refused here too.
"""

import gzip
import hashlib
import re

import httpx
from click.testing import CliRunner
from postgrest import APIError
from scrna_fake_db import FakeClient

from bloomctl.cli import cli
from bloomctl.genome import _index

GENOME = "tair10_araport11"
FASTA_GZ = gzip.compress(b">Chr1\nACGTACGTACGGGGCCCCAA\n>Chr2\nTTTTAAAAGG\n", mtime=0)
GTF_GZ = gzip.compress(
    b'Chr1\tAraport11\tgene\t3\t9\t.\t+\t.\tgene_id "AT1G01010"; gene_name "NAC001";\n'
    b'Chr1\tAraport11\texon\t12\t18\t.\t-\t.\tgene_id "AT1G01020"; transcript_id "t2";\n'
    b'Chr2\tAraport11\tgene\t1\t4\t.\t-\t.\tgene_id "AT2G01010";\n',
    mtime=0,
)
ME = "writer-uid"
GENE_KEYS = {"gene_id", "gene_name", "contig", "gene_start", "gene_end", "strand"}
_SHA256 = re.compile(r"[0-9a-f]{64}")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def refusal(message, code="22023"):
    return APIError({"message": message, "code": code})


class _Call:
    def __init__(self, run):
        self._run = run

    def execute(self):
        return type("R", (), {"data": self._run()})()


def _blank(value):
    return not isinstance(value, str) or not value.strip()


def _gene_ok(gene) -> bool:
    """genome_browser_genes' column checks."""
    name, contig = gene.get("gene_name"), gene.get("contig")
    start, end = gene.get("gene_start"), gene.get("gene_end")
    return (
        not _blank(gene.get("gene_id")) and len(gene["gene_id"]) <= 200
        and (name is None or (not _blank(name) and len(name) <= 200))
        and isinstance(contig, str) and re.fullmatch(r"\S+", contig) is not None
        and len(contig) <= 200
        and type(start) is int and type(end) is int and 1 <= start <= end
        and gene.get("strand") in ("+", "-", ".")
    )


def files_ok(files) -> bool:
    """genome_browser_files_ok: exactly the five roles, each {"sha256", "bytes"} and no more."""
    if not isinstance(files, dict) or set(files) != set(_index.ROLES):
        return False
    return all(
        isinstance(f, dict) and set(f) == {"sha256", "bytes"}
        and isinstance(f["sha256"], str) and _SHA256.fullmatch(f["sha256"]) is not None
        and type(f["bytes"]) is int and 1 <= f["bytes"] < 10**18
        for f in files.values()
    )


class BrowserDB:
    """The genome tables and the browser-build functions, kept in memory."""

    def __init__(self, storage, version_status="ready"):
        self.storage = storage
        version = {
            "id": 101, "genome_id": 1, "version": 1, "status": version_status,
            "withdrawn_reason": None,
            "fasta_path": f"{GENOME}/v1/genome.fa.gz", "fasta_sha256": sha(FASTA_GZ),
            "fasta_bytes": len(FASTA_GZ),
            "gtf_path": f"{GENOME}/v1/genes.gtf.gz", "gtf_sha256": sha(GTF_GZ),
            "gtf_bytes": len(GTF_GZ),
        }
        self.base = FakeClient({
            "genome_references": [{"id": 1, "name": GENOME}],
            "genome_reference_versions": [version],
            "genome_browser_builds": [],
        })
        self.version = self.base.tables["genome_reference_versions"][0]
        self.builds = self.base.tables["genome_browser_builds"]
        self.genes: dict[int, dict[str, dict]] = {}
        self.calls: list[tuple[str, dict]] = []
        # Each name: the answers its next calls give instead of running (an exception each).
        self.fail: dict[str, list[BaseException]] = {}
        # Each name: how many calls take effect, then fail as a lost connection.
        self.lose_answer: dict[str, int] = {}

    def table(self, name):
        return self.base.table(name)

    def rpc(self, name, args):
        def run():
            self.calls.append((name, args))
            if self.fail.get(name):
                raise self.fail[name].pop(0)
            result = getattr(self, name)(**args)
            if self.lose_answer.get(name, 0) > 0:
                self.lose_answer[name] -= 1
                raise httpx.ReadTimeout("the answer was lost")
            return result

        return _Call(run)

    def called(self, name):
        return [a for n, a in self.calls if n == name]

    def add_build(self, status, created_by=ME, **extra):
        build = {"id": 7 + len(self.builds), "version_id": 101, "build": len(self.builds) + 1,
                 "status": status, "folder": f"{GENOME}/v1/browser/b{len(self.builds) + 1}",
                 "created_by": created_by, "gene_count": None, **extra}
        self.builds.append(build)
        self.genes[build["id"]] = {}
        return build

    def _version_ready(self):
        if self.version["status"] != "ready":
            raise refusal(f"genome version {self.version['id']} is {self.version['status']}, "
                          "not ready", "55000")

    def _own(self, build_id):
        build = next((b for b in self.builds if b["id"] == build_id), None)
        if build is None:
            raise refusal(f"no browser build {build_id}")
        if build["created_by"] != ME:
            raise refusal(f"browser build {build_id} was started by someone else", "42501")
        return build

    def _uploading(self, build_id):
        build = self._own(build_id)
        if build["status"] != "uploading":
            raise refusal(f"browser build {build_id} is {build['status']}, not uploading",
                          "55000")
        return build

    def start_genome_browser_build(self, p_version_id, p_tool_versions):
        if not isinstance(p_tool_versions, dict):
            raise refusal("tool versions must be a JSON object")
        live = next((b for b in self.builds if b["status"] in ("uploading", "ready")), None)
        if live:
            raise refusal(f"genome version {p_version_id} already has browser build "
                          f"{live['build']} ({live['status']})", "55000")
        self._version_ready()
        build = self.add_build("uploading", tool_versions=p_tool_versions)
        return [{"build_id": build["id"], "build": build["build"], "folder": build["folder"]}]

    def add_genome_browser_genes(self, p_build_id, p_genes):
        self._uploading(p_build_id)
        if not isinstance(p_genes, list) or not 1 <= len(p_genes) <= 5000:
            raise refusal("send 1 to 5000 genes at a time, as a JSON array")
        ids = [g.get("gene_id") for g in p_genes]
        repeated = sorted({i for i in ids if i is not None and ids.count(i) > 1})
        if repeated:
            raise refusal(f"gene {repeated[0]} appears more than once; a browser build holds "
                          "one position per gene")
        held, added = self.genes[p_build_id], 0
        for gene in p_genes:
            if set(gene) != GENE_KEYS or not _gene_ok(gene):
                raise refusal("new row violates check constraint", "23514")
            if gene["gene_id"] in held:
                if held[gene["gene_id"]] != gene:
                    raise refusal(f"gene {gene['gene_id']} was already added to browser build "
                                  f"{p_build_id} with different details", "23505")
                continue
            held[gene["gene_id"]] = gene
            added += 1
        return added

    def finish_genome_browser_build(self, p_build_id, p_files, p_gene_count):
        build = self._uploading(p_build_id)
        if not files_ok(p_files):
            raise refusal("a browser build records exactly the five files, each with a 64 "
                          "lowercase hex sha256 and a positive bytes")
        for role, name in _index.ROLES.items():
            stored = self.storage.objects.get(f"genome-references/{build['folder']}/{name}")
            if stored is None:
                raise refusal(f"{build['folder']}/{name} has not been uploaded")
            if len(stored) != p_files[role]["bytes"]:
                raise refusal(f"{build['folder']}/{name} is {len(stored)} bytes in storage, "
                              f"not {p_files[role]['bytes']}")
        if type(p_gene_count) is not int or p_gene_count < 1:
            raise refusal("a browser build records at least one gene")
        if p_gene_count != len(self.genes[p_build_id]):
            raise refusal(f"browser build {p_build_id} holds {len(self.genes[p_build_id])} "
                          f"genes, not {p_gene_count}")
        self._version_ready()
        build.update(status="ready", files=p_files, gene_count=p_gene_count)
        return build["build"]

    def abandon_genome_browser_build(self, p_build_id):
        build = self._own(p_build_id)
        if build["status"] == "abandoned":
            return False
        if build["status"] != "uploading":
            raise refusal(f"browser build {p_build_id} is {build['status']}, not uploading",
                          "55000")
        build["status"] = "abandoned"
        self.genes[p_build_id] = {}
        return True


def stock(env, storage) -> BrowserDB:
    """Sign the command in to a BrowserDB, with the version's two files in storage."""
    env["client"] = BrowserDB(storage)
    storage.objects[f"genome-references/{GENOME}/v1/genome.fa.gz"] = FASTA_GZ
    storage.objects[f"genome-references/{GENOME}/v1/genes.gtf.gz"] = GTF_GZ
    return env["client"]


def run_index(*args):
    return CliRunner().invoke(cli, ["genome", "index", *args])


def replace_gtf(db, storage, gtf: bytes):
    data = gzip.compress(gtf, mtime=0)
    storage.objects[f"genome-references/{GENOME}/v1/genes.gtf.gz"] = data
    db.version.update(gtf_sha256=sha(data), gtf_bytes=len(data))
