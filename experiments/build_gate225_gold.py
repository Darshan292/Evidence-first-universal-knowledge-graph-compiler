"""Derive Gate 2.25 gold from corpus3 by an INDEPENDENT AST pass.

This script must never import kgc. Ground truth is read from the raw source with
the standard library, so the gold does not inherit the implementation's view.
"""
from __future__ import annotations
import ast, hashlib, json, random, sys
from collections import defaultdict
from pathlib import Path

assert "kgc" not in sys.modules, "gold generation must not use the implementation"

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "eval/corpus3"
random.seed(20260923)


def module_name(rel: Path) -> str:
    parts = list(rel.with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def scan():
    """Independent ground truth: classes, functions, class-level constants, bases."""
    classes, funcs, consts, bases, imports = [], [], [], [], []
    for p in sorted(CORPUS.rglob("*.py")):
        rel = p.relative_to(CORPUS)
        mod = module_name(rel)
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                classes.append({"name": node.name, "module": mod, "file": str(rel),
                                "line": node.lineno, "doc": ast.get_docstring(node)})
                for b in node.bases:
                    if isinstance(b, ast.Name):
                        bases.append({"child": node.name, "base": b.id,
                                      "module": mod, "file": str(rel)})
                for sub in node.body:
                    tgts = (sub.targets if isinstance(sub, ast.Assign)
                            else [sub.target] if isinstance(sub, ast.AnnAssign) else [])
                    val = getattr(sub, "value", None)
                    for tg in tgts:
                        if isinstance(tg, ast.Name) and isinstance(
                                val, (ast.Constant, ast.Name, ast.Attribute)):
                            try:
                                lit = ast.unparse(val)
                            except Exception:
                                continue
                            consts.append({"cls": node.name, "attr": tg.id, "value": lit,
                                           "module": mod, "file": str(rel),
                                           "line": sub.lineno})
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.append({"name": node.name, "module": mod, "file": str(rel),
                              "line": node.lineno, "doc": ast.get_docstring(node)})
            elif isinstance(node, ast.ImportFrom) and node.level:
                base = mod.split(".")[:-node.level]
                target = ".".join([*base, node.module]) if node.module else ".".join(base)
                for a in node.names:
                    imports.append({"module": mod, "target": f"{target}.{a.name}",
                                    "name": a.name, "file": str(rel)})
    return classes, funcs, consts, bases, imports


def unique_by(items, key):
    seen = defaultdict(list)
    for it in items:
        seen[key(it)].append(it)
    return {k: v for k, v in seen.items() if len(v) == 1}, \
           {k: v for k, v in seen.items() if len(v) > 1}


def main():
    classes, funcs, consts, bases, imports = scan()
    uniq_cls, dup_cls = unique_by(classes, lambda c: c["name"])
    uniq_fn, dup_fn = unique_by(funcs, lambda f: f["name"])
    uniq_const, _ = unique_by(consts, lambda c: (c["cls"], c["attr"]))

    q, n = [], 0

    def add(cls, query, answerable, units=None, ev=None, target=None, why=None):
        nonlocal n
        n += 1
        r = {"id": f"G225-{n:04d}", "class": cls, "query": query, "answerable": answerable,
             "expected_decision": (["EXPOSE", "EXPOSE_CONFLICTED"] if answerable
                                   else ["ABSTAIN", "ABSTAIN_AMBIGUOUS"])}
        if answerable:
            r["answer_units"] = units; r["evidence_must_contain"] = ev
            r["claim_target"] = target
        else:
            r["rationale"] = why
        q.append(r)

    # ---------- POSITIVES ----------
    for c in random.sample(sorted(uniq_cls.values(), key=lambda v: v[0]["name"]), 14):
        c = c[0]
        add("exact_lookup", c["name"], True, [c["file"]], f"class {c['name']}",
            {"entity": c["name"], "property": "definition"})

    for f in random.sample(sorted(uniq_fn.values(), key=lambda v: v[0]["name"]), 10):
        f = f[0]
        add("exact_lookup", f["name"], True, [f["file"]], f"def {f['name']}",
            {"entity": f["name"], "property": "definition"})

    cand = [v[0] for v in uniq_const.values()
            if v[0]["cls"] in uniq_cls and len(v[0]["value"]) < 40]
    for c in random.sample(sorted(cand, key=lambda x: (x["cls"], x["attr"])), min(16, len(cand))):
        add("property_lookup", f"what is the {c['attr']} of {c['cls']}", True,
            [c["file"]], c["attr"], {"entity": c["cls"], "property": c["attr"]})

    bcand = [b for b in bases if b["child"] in uniq_cls and b["base"] in uniq_cls]
    for b in random.sample(sorted(bcand, key=lambda x: x["child"]), min(12, len(bcand))):
        add("structural_relation", f"which class does {b['child']} extend", True,
            [b["file"]], f"class {b['child']}",
            {"entity": b["child"], "relation": "EXTENDS", "object": b["base"]})

    icand = [i for i in imports if i["name"] in uniq_cls or i["name"] in uniq_fn]
    for i in random.sample(sorted(icand, key=lambda x: x["module"]), min(10, len(icand))):
        add("cross_file_relation", f"what does {i['module']} import", True,
            [i["file"]], "import", {"entity": i["module"], "relation": "IMPORTS",
                                    "object": i["target"]})

    # ---------- NEGATIVES ----------
    fake = ["RedisSessionStore", "JWTSigner", "GraphQLRouter", "KafkaProducer",
            "OAuthMiddleware", "TenantResolver", "ShardCoordinator", "WebhookDispatcher"]
    for name in fake:
        add("unsupported_entity", f"what is the default timeout of {name}", False,
            why=f"{name} does not exist in the corpus")

    for c in random.sample(sorted(uniq_cls.values(), key=lambda v: v[0]["name"]), 10):
        c = c[0]
        add("wrong_property", f"what is the encryption_key of {c['name']}", False,
            why=f"{c['name']} exists; encryption_key is not one of its attributes")

    for c in random.sample(sorted(cand, key=lambda x: (x["cls"], x["attr"])), min(10, len(cand))):
        add("wrong_value", f"is the {c['attr']} of {c['cls']} equal to 987654", False,
            why=f"real value is {c['value']!r}; 987654 appears nowhere")

    for b in random.sample(sorted(bcand, key=lambda x: x["child"]), min(8, len(bcand))):
        add("wrong_relationship", f"does {b['child']} extend KafkaProducer", False,
            why=f"{b['child']} extends {b['base']}, not a nonexistent class")

    for c in random.sample(sorted(cand, key=lambda x: (x["cls"], x["attr"])), min(8, len(cand))):
        wrong = "pyproject.toml" if not c["file"].endswith("pyproject.toml") else "README.md"
        add("source_scope", f"is {c['attr']} of {c['cls']} defined in {wrong}", False,
            why=f"it is defined in {c['file']}")

    for name, dups in list(sorted(dup_cls.items()))[:8]:
        add("ambiguous_entity", f"what is the name of {name}", False,
            why=f"{name} is defined in {len(dups)} modules "
                f"({[d['module'] for d in dups][:3]}) with no disambiguator")

    # ---------- prose / rationale / malformed ----------
    for c in random.sample(sorted(uniq_cls.values(), key=lambda v: v[0]["name"]), 6):
        c = c[0]
        add("rationale_prose", f"why was {c['name']} designed this way", False,
            why="rationale is not a compiled claim; deterministic scope excludes it")
    for text in ["how does the routing system work overall",
                 "explain the middleware architecture",
                 "what are the tradeoffs of this design"]:
        add("rationale_prose", text, False, why="document-level interpretation required")
    for text in ["???", "the the the", "give me everything", "asdf qwer zxcv"]:
        add("malformed", text, False, why="not a well-formed question")
    for c in random.sample(sorted(uniq_cls.values(), key=lambda v: v[0]["name"]), 5):
        c = c[0]
        add("paraphrase", f"which component handles {c['name'].lower()} behaviour", False,
            why="paraphrase without typed constraints; outside deterministic scope")

    # ---------- split ----------
    strata = defaultdict(list)
    for r in q:
        strata[(r["answerable"], r["class"])].append(r)
    cyc = ["CALIBRATION", "VALIDATION", "TEST"]
    for key, g in strata.items():
        g.sort(key=lambda r: hashlib.sha256(f"g225:{r['id']}".encode()).hexdigest())
        off = int(hashlib.sha256(str(key).encode()).hexdigest(), 16) % 3
        for i, r in enumerate(g):
            r["split"] = cyc[(i + off) % 3]

    doc = {"version": "1.0.0", "authored": "2026-09-23",
           "corpus": "eval/corpus3 (werkzeug @ 6048fa48753c7b61e35cc34537667809dee8fa35, BSD-3-Clause)",
           "corpus_root": "eval/corpus3",
           "provenance": ("Ground truth derived from the raw source by an independent "
                          "stdlib-ast pass (this script, which must not import kgc). "
                          "Negatives are perturbations of verified real facts. No output "
                          "of the implementation under test was consulted."),
           "queries": q}
    (ROOT / "eval/GATE225_GOLD.json").write_text(json.dumps(doc, indent=2))

    from collections import Counter
    print(f"corpus classes={len(classes)} funcs={len(funcs)} consts={len(consts)} "
          f"bases={len(bases)} rel-imports={len(imports)}")
    print(f"unique class names={len(uniq_cls)} duplicated={len(dup_cls)}")
    print(f"\nqueries: {len(q)}  answerable={sum(1 for r in q if r['answerable'])} "
          f"unanswerable={sum(1 for r in q if not r['answerable'])}")
    for cls, cnt in sorted(Counter(r["class"] for r in q).items()):
        print(f"   {cls:24} {cnt}")
    print()
    for s in cyc:
        sub = [r for r in q if r["split"] == s]
        print(f"   {s:12} n={len(sub):3} pos={sum(1 for r in sub if r['answerable']):3} "
              f"neg={sum(1 for r in sub if not r['answerable']):3}")


if __name__ == "__main__":
    main()
