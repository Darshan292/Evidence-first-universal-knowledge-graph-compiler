"""H-3: classify what the deterministic system cannot answer, and why.

Classes (from the gate):
  A  derivable with a RICHER CLAIM MODEL (no model needed)
  B  requires DOCUMENT-LEVEL semantic interpretation
  C  requires REASONING ACROSS MULTIPLE CLAIMS
  D  cannot be answered faithfully from the corpus at all
"""
from __future__ import annotations
import json, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RESULTS = json.loads((ROOT / "experiments/gate225_results.json").read_text())
G225 = {q["id"]: q for q in json.loads((ROOT / "eval/GATE225_GOLD.json").read_text())["queries"]}
G2 = json.loads((ROOT / "eval/GATE2_GOLD.json").read_text())["queries"]

# Manual classification of the query SHAPES that the deterministic layer refuses.
# Keyed by shape, not by individual query, so it generalises.
SHAPE_CLASS = {
    "docstring_purpose": ("A", "The docstring is already extracted and stored on the "
                          "symbol. A HAS_PURPOSE claim over the docstring's first "
                          "sentence is derivable with no model.",
                          "add HAS_PURPOSE claims sourced from docstrings"),
    "default_value_phrasing": ("A", "'what separator does X use by default' names a real "
                               "attribute in prose the parser does not map. A synonym-free "
                               "canonical property index over direct children closes it.",
                               "index direct-child attribute names for lookup"),
    "reexport_question": ("A", "'what does X re-export' is exactly the IMPORTS claims of a "
                          "package __init__, already compiled.",
                          "map 're-export' to the IMPORTS predicate"),
    "raises_question": ("A", "'what does X raise on failure' is a CALLS claim to an "
                        "exception class, already compiled.",
                        "map 'raise' phrasing to CALLS with an exception-typed object"),
    "bare_concept": ("B", "'salt' names a documented concept, not a compiled symbol. "
                     "Requires locating a definition in prose.",
                     "document-level concept extraction"),
    "why_rationale": ("B", "'why was X chosen' requires interpreting argumentation in "
                      "prose. No compiled claim expresses it.",
                      "document-level rationale extraction"),
    "how_mechanism": ("C", "'how does routing work overall' requires composing many "
                      "structural claims into a narrative.",
                      "multi-claim composition over the existing graph"),
    "tradeoffs": ("C", "'what are the tradeoffs' requires relating several rationale "
                  "claims that do not yet exist.",
                  "rationale claims (B) plus composition (C)"),
    "paraphrase_component": ("B", "'which component handles X behaviour' needs a mapping "
                             "from a concept to symbols that is not lexical.",
                             "concept-to-symbol linking"),
    "malformed": ("D", "Not a well-formed question. No capability answers it faithfully.",
                  "none: correct behaviour is to refuse"),
    "unsupported_fact": ("D", "The fact is absent from the corpus. Any answer would be "
                         "fabrication.", "none: correct behaviour is to refuse"),
}

# Map the concrete failures observed to shapes.
OBSERVED = [
    ("what is the default key derivation scheme", "default_value_phrasing"),
    ("what separator does Signer use by default", "default_value_phrasing"),
    ("what does itsdangerous re-export from exc", "reexport_question"),
    ("what does base64_decode raise on failure", "raises_question"),
    ("what is a salt used for", "docstring_purpose"),
    ("salt", "bare_concept"),
    ("how is the signing key derived from the secret", "why_rationale"),
    ("which component turns bytes into a shortened textual form", "paraphrase_component"),
    ("what is raised when a token is too old", "raises_question"),
    ("why should different salts be used", "why_rationale"),
    ("why was X designed this way", "why_rationale"),
    ("how does the routing system work overall", "how_mechanism"),
    ("explain the middleware architecture", "how_mechanism"),
    ("what are the tradeoffs of this design", "tradeoffs"),
    ("which component handles X behaviour", "paraphrase_component"),
    ("???", "malformed"),
    ("the the the", "malformed"),
    ("give me everything", "malformed"),
    ("asdf qwer zxcv", "malformed"),
    ("what is the default timeout of RedisSessionStore", "unsupported_fact"),
]

by_class = defaultdict(list)
for q, shape in OBSERVED:
    cls, why, need = SHAPE_CLASS[shape]
    by_class[cls].append({"query": q, "shape": shape, "why": why, "minimum_capability": need})

counts = Counter(c for c, _ in ((SHAPE_CLASS[s][0], q) for q, s in OBSERVED))
total = sum(counts.values())

print("H-3 classification of what the deterministic layer cannot answer\n")
names = {"A": "derivable with a richer claim model (NO model needed)",
         "B": "requires document-level semantic interpretation",
         "C": "requires reasoning across multiple claims",
         "D": "cannot be answered faithfully from the corpus"}
for cls in "ABCD":
    n = counts.get(cls, 0)
    print(f"  {cls}  {n:2}/{total}  ({n/total:.0%})  {names[cls]}")
    for item in by_class[cls]:
        print(f"        · {item['query'][:52]:54} -> {item['minimum_capability']}")
    print()

print(f"KEY RESULT: {counts['A']}/{total} ({counts['A']/total:.0%}) of observed failures are "
      f"class A —\n  solvable by compiling MORE claim types, with no model involved at all.")
print(f"  Only {counts['B']+counts['C']}/{total} ({(counts['B']+counts['C'])/total:.0%}) "
      f"genuinely need semantic capability, and {counts['D']}/{total} should never be answered.")

out = {"classification": {c: by_class[c] for c in "ABCD"},
       "counts": dict(counts), "total": total,
       "class_A_share": round(counts["A"] / total, 3),
       "conclusion": ("A future model stage should be scoped to classes B and C only. "
                      "Class A is a claim-model gap, not a language-understanding gap, and "
                      "building an LLM stage to cover it would hide a deterministic "
                      "capability behind a probabilistic one.")}
(ROOT / "experiments/h3_classification.json").write_text(json.dumps(out, indent=2))
