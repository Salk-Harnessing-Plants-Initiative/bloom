"""The GO ontology (go-basic.obo) and KEGG pathway names, as the annotation step reads them."""

from __future__ import annotations

from dataclasses import dataclass, field

# GO namespaces as the kinds the annotation and enrichment files use.
KINDS = {
    "biological_process": "GO_BP",
    "molecular_function": "GO_MF",
    "cellular_component": "GO_CC",
}
KEGG = "KEGG"
# How many old-ID steps resolve() follows (an obsolete term replaced by one later merged).
MAX_ALIAS_HOPS = 5


@dataclass
class Ontology:
    """Live GO terms by ID as (kind, name), each term's is_a and part_of parents, older IDs that
    now point to a live term (alt_id, and an obsolete term's replaced_by), and the release."""

    terms: dict[str, tuple[str, str]] = field(default_factory=dict)
    parents: dict[str, set[str]] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    version: str | None = None
    _ancestors: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)

    def resolve(self, term: str) -> str | None:
        """The live term an ID means today: itself, the term it was merged into (alt_id), or the
        term that replaced it (replaced_by); None for an unknown ID or one only with a consider."""
        for _ in range(MAX_ALIAS_HOPS):
            if term in self.terms:
                return term
            if term not in self.aliases:
                return None
            term = self.aliases[term]
        return term if term in self.terms else None

    def ancestors(self, term: str) -> frozenset[str]:
        """The term and every term above it."""
        if term not in self._ancestors:
            found, todo = {term}, [term]
            while todo:
                for parent in self.parents.get(todo.pop(), ()):
                    if parent not in found:
                        found.add(parent)
                        todo.append(parent)
            self._ancestors[term] = frozenset(found)
        return self._ancestors[term]


def parse_obo(text: str) -> Ontology:
    """go-basic.obo's [Term] stanzas; obsolete terms and other stanzas are left out, but their
    alt_id and replaced_by lines are kept as aliases."""
    ontology = Ontology()
    stanza: dict | None = None

    def keep(term: dict | None) -> None:
        if not term or "id" not in term:
            return
        if term.get("obsolete"):
            if term.get("replaced_by"):
                ontology.aliases[term["id"]] = term["replaced_by"]
        elif {"name", "kind"} <= term.keys():
            ontology.terms[term["id"]] = (term["kind"], term["name"])
            ontology.parents[term["id"]] = term["parents"]
            for alt in term["alt_ids"]:
                ontology.aliases[alt] = term["id"]

    for line in text.splitlines():
        if line.startswith("data-version:") and stanza is None:
            ontology.version = line.split(":", 1)[1].strip()
        elif line.startswith("["):
            keep(stanza)
            stanza = {"parents": set(), "alt_ids": []} if line == "[Term]" else None
        elif stanza is not None and ": " in line:
            key, value = line.split(": ", 1)
            if key in ("id", "name"):
                stanza[key] = value
            elif key == "namespace":
                stanza["kind"] = KINDS.get(value)
            elif key == "is_obsolete" and value == "true":
                stanza["obsolete"] = True
            elif key == "alt_id":
                stanza["alt_ids"].append(value.strip())
            elif key == "replaced_by":
                stanza["replaced_by"] = value.split()[0]
            elif key == "is_a":
                stanza["parents"].add(value.split()[0])
            elif key == "relationship" and value.startswith("part_of "):
                stanza["parents"].add(value.split()[1])
    keep(stanza)
    return ontology


def parse_kegg_list(text: str) -> dict[str, str]:
    """KEGG's pathway list for one species (`<org><5 digits>\\t<name> - <species>`), by map ID."""
    names = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        pathway, name = line.split("\t", 1)
        names["map" + pathway[-5:]] = name.rsplit(" - ", 1)[0].strip()
    return names
