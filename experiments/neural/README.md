# Neural embedding experiment — external execution

**Status: NOT RUN. ENVIRONMENTALLY BLOCKED.**

## Why this is not run here

Environment inspected 2026-09-22:

| Checked | Result |
|---|---|
| `ollama` binary | absent |
| `~/.ollama` | absent |
| `~/.cache/huggingface`, `~/.cache/torch`, `~/.cache/sentence_transformers`, `~/.cache/fastembed` | all absent |
| `*.gguf`, `*.onnx`, `*.safetensors`, `pytorch_model.bin` anywhere on disk (>1 MB) | **none found** |
| `sentence_transformers`, `fastembed`, `onnxruntime`, `transformers`, `torch`, `gensim` | none installed |
| `huggingface.co` reachability | HTTP 000 (blocked), confirmed twice |

**No local model exists and none can be fetched.** Per the gate's instruction,
the local run stops here and **LSA is not substituted a second time.**

## What this experiment must answer

Gate 1.5 left one question open and X1-RERUN-01 has now quantified it precisely:

> On the 10 **mechanically verified zero-vocabulary-overlap** paraphrase queries,
> lexical BM25 achieves **Recall@10 = 0.1** (5 of 10 return an empty result set)
> and corpus-derived LSA achieves **0.2**. Does a *pretrained* neural embedding —
> whose semantic space is learned from general text rather than from this corpus —
> materially close that gap?

The target is explicit: **Recall@10 ≥ 0.8 on the `zero_overlap` tier**, which is
what D-B's `ΔR@10 ≥ 0.10` would require against the measured 0.1 baseline.

## Environment requirements

- Python 3.11+
- `pip install fastembed==0.8.0` (Apache-2.0, ONNX runtime, **no torch**)
- Network access to the model host on first run only; fully offline afterwards
- ~2 GB free disk, no GPU required

## Model to use

| Field | Value |
|---|---|
| Identifier | `BAAI/bge-small-en-v1.5` |
| Dimensions | 384 |
| Approx. size | ~130 MB |
| Why this one | small, CPU-only, permissively licensed, widely used as a retrieval baseline |

Record the **exact resolved revision** and the SHA-256 of every downloaded model
file. `run_neural_experiment.py` **fails hard** if it cannot: the result is written
with `validity.status = "INVALID"`, `decision.met` is forced to `false`, and the
process exits non-zero.

> **An unattributable neural result is not admissible** as evidence for the
> dense-retrieval decision. Set `MODEL_REVISION` to the exact resolved revision
> and ensure the model cache is reachable via `--model-dir` or
> `FASTEMBED_CACHE_PATH`.

Hashed file types: `.onnx`, `.onnx_data`, `.json`, `.txt`, `.model`, `.bin`,
`.safetensors`, `.vocab`, `.merges`, `.spm`, plus `tokenizer.json`,
`config.json` and `special_tokens_map.json` by name — the files the ONNX runtime
actually downloads and opens.

If model files are supplied manually instead, place them in `--model-dir` and the
script will hash them the same way.

## Commands

```bash
git clone <this repo> && cd Evidence-first-universal-knowledge-graph-compiler
python3 -m venv .neural && . .neural/bin/activate
pip install fastembed==0.8.0 numpy

python3 experiments/neural/run_neural_experiment.py \
    --model BAAI/bge-small-en-v1.5 \
    --out experiments/neural/neural_results.json
```

The script depends only on this repository and the corpus committed in it. It
does **not** depend on the development environment: no local database, no
pre-built index, no `.evalenv`.

## Input

- `eval/corpus/` — the frozen corpus (15 files)
- `eval/x1rerun/X1_RERUN_01_GOLD.json` — 17 queries, 10 of them verified
  zero-overlap

## Output schema

```json
{
  "model": {"identifier": "...", "revision": "...", "dimensions": 384,
            "files": [{"path": "...", "bytes": 0, "sha256": "..."}],
            "total_bytes": 0, "offline_after_first_run": true},
  "cost": {"embed_corpus_seconds": 0.0, "chunks": 0,
           "embed_query_ms_p50": 0.0, "embed_query_ms_p95": 0.0,
           "index_build_seconds": 0.0, "index_bytes": 0},
  "results": {"<tier>": {"n": 0, "recall@1": 0.0, "recall@10": 0.0,
                          "MRR": 0.0, "empty_result_sets": 0}},
  "comparison_to_local_run": {"R1_bm25": {...}, "R3_lsa": {...}},
  "adversarial": {"distractor_beats_gold": 0.0},
  "decision": {"threshold": "zero_overlap recall@10 >= 0.8",
               "met": false, "note": "filled by the script, not by hand"}
}
```

## Decision rule — fixed in advance

Adopt dense retrieval **only if all four hold**:

1. `zero_overlap` Recall@10 **≥ 0.8** (baseline: BM25 0.1, LSA 0.2);
2. no regression on `full_overlap` (BM25 is at 1.0 there);
3. `distractor_beats_gold` **≤ 0.0** — Gate 1.5 measured LSA making this
   adversarial case *worse* (0.0 → 1.0), so a neural model must not repeat it;
4. corpus embedding **≤ 60 s** and query p95 **≤ 200 ms** on CPU.

**Even if all four hold, this authorizes an embedding index — not a vector
database.** See `GATE_1_75_REPORT.md`.
