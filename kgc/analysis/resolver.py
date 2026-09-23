"""Corpus-level cross-module reference resolution.

Per-file analysis cannot know whether `requests.get` is a real target or an
absent third-party symbol. This pass resolves references against the symbol
table of the *whole corpus*, which is already persisted after the extract stage.

Two rules govern it, and the second matters more than the first:

  1. Resolve only what can be proven from the corpus.
  2. A local definition ALWAYS shadows an imported name. Resolving a shadowed
     call to the import produces a wrong deterministic edge -- worse than an
     honest UNRESOLVED (pre-registration rule D-H).

An import whose target module is not in the corpus resolves to UNRESOLVED, not
HEURISTIC: we know the name, we cannot reach the definition, and claiming
otherwise overstates what the analysis established.
"""
from __future__ import annotations

from dataclasses import dataclass

from kgc.ir import Resolution

RESOLVER_VERSION = "1.0.0"


@dataclass
class ModuleIndex:
    """Corpus-wide symbol table.

    `modules`: module qname -> {local name -> qualified name}
    `symbol_ids`: qualified name -> symbol_id, so a claim can reference a symbol
    in ANOTHER artifact. Without this the mapper can only link within one file,
    which produced DETERMINISTIC references carrying a null target.
    """
    modules: dict[str, dict[str, str]]
    symbol_ids: dict[str, str]

    @classmethod
    def from_store(cls, store) -> "ModuleIndex":
        mods: dict[str, dict[str, str]] = {}
        ids: dict[str, str] = {}
        rows = store.con.execute(
            "SELECT s.symbol_id, s.qualified_name, s.kind, p.qualified_name AS parent_qn"
            "  FROM symbol s LEFT JOIN symbol p ON p.symbol_id = s.parent_id")
        for r in rows:
            ids.setdefault(r["qualified_name"], r["symbol_id"])
            if r["kind"] == "module":
                mods.setdefault(r["qualified_name"], {})
            elif r["parent_qn"]:
                mods.setdefault(r["parent_qn"], {})[r["qualified_name"].rsplit(".", 1)[-1]] = \
                    r["qualified_name"]
        return cls(mods, ids)

    def has_module(self, m: str) -> bool:
        return m in self.modules

    def lookup(self, module: str, name: str) -> str | None:
        return self.modules.get(module, {}).get(name)


def resolve(analysis, module_name: str, index: ModuleIndex, reexports: dict[str, str]):
    """Return a new reference list with resolution upgraded or corrected.

    `reexports` maps a dotted name exposed by a package to its true origin,
    derived from package __init__ bindings collected in the same pass.
    """
    out = []
    for ref in analysis.references:
        if ref.predicate != "CALLS":
            out.append(_resolve_import(ref, index, analysis))
            continue
        out.append(_resolve_call(ref, module_name, index, analysis, reexports))
    return out


def _resolve_import(ref, index: ModuleIndex, analysis):
    """An import of a module absent from the corpus is UNRESOLVED, not HEURISTIC."""
    if ref.resolution is Resolution.UNRESOLVED:
        return ref
    target = ref.to_name
    module = target.rsplit(".", 1)[0] if "." in target else target
    if index.has_module(target) or index.has_module(module):
        return _with(ref, Resolution.HEURISTIC, ref.to_qname,
                     "import target module is in the corpus")
    return _with(ref, Resolution.UNRESOLVED, None,
                 f"import target {target!r} is not in the analysed corpus")


def _resolve_call(ref, module_name, index: ModuleIndex, analysis, reexports):
    name = ref.to_name
    head = name.split(".")[0]

    # Rule 2 first: a module-level definition shadows any imported name.
    if head in analysis.module_defs and "." not in name:
        q = index.lookup(module_name, name)
        if q:
            return _with(ref, Resolution.DETERMINISTIC, q,
                         "bound to a module-level definition (shadows any import)")
        return ref

    origin = analysis.bindings.get(head)
    if origin is None:
        return _with(ref, Resolution.UNRESOLVED, None,
                     "not bound in any statically known scope")

    if "." in name:                       # qualified: alias.attr or pkg.mod.attr
        attr = name.split(".", 1)[1]
        # `import pkg.core` binds head 'pkg' -> 'pkg.core'; `import pkg.core as c`
        # binds 'c' -> 'pkg.core'. Both reach the module by joining what remains.
        for candidate in _module_candidates(origin, name, attr):
            mod, sym = candidate
            q = index.lookup(mod, sym)
            if q:
                return _with(ref, Resolution.DETERMINISTIC, q,
                             f"qualified call resolved to {mod}.{sym} in the corpus")
        if not index.has_module(origin):
            return _with(ref, Resolution.UNRESOLVED, None,
                         f"module {origin!r} is not in the analysed corpus")
        return _with(ref, Resolution.UNRESOLVED, None,
                     "qualified target not found in the corpus")

    # bare name bound by `from X import y` (origin == "X.y")
    if "." in origin:
        mod, sym = origin.rsplit(".", 1)
        q = index.lookup(mod, sym)
        if q:
            return _with(ref, Resolution.DETERMINISTIC, q,
                         f"from-import binding resolved to {mod}.{sym}")
        if origin in reexports:
            true_origin = reexports[origin]
            q2 = index.lookup(*true_origin.rsplit(".", 1))
            if q2:
                return _with(ref, Resolution.HEURISTIC, q2,
                             f"resolved through a re-export in {mod}")
        if not index.has_module(mod):
            return _with(ref, Resolution.UNRESOLVED, None,
                         f"module {mod!r} is not in the analysed corpus")
    return _with(ref, Resolution.UNRESOLVED, None,
                 "imported name has no definition in the analysed corpus")


def _module_candidates(origin, full_name, attr):
    """Possible (module, symbol) splits for a qualified call."""
    yield origin, attr                                   # import pkg.core as c ; c.helper
    if "." in attr:                                      # import pkg ; pkg.core.helper
        mid, last = attr.rsplit(".", 1)
        yield f"{origin}.{mid}", last
    yield full_name.rsplit(".", 1)[0], full_name.rsplit(".", 1)[1]


def _with(ref, resolution, to_qname, reason):
    from dataclasses import replace
    return replace(ref, resolution=resolution, to_qname=to_qname, reason=reason)


def collect_reexports(analyses: dict[str, object]) -> dict[str, str]:
    """`pkg.helper` -> `pkg.core.helper` for names a package __init__ re-exports."""
    out: dict[str, str] = {}
    for module_name, an in analyses.items():
        for local, origin in getattr(an, "bindings", {}).items():
            out[f"{module_name}.{local}"] = origin
    return out
