"""J-2 runner. Executes the FROZEN system over the FROZEN query set.

It changes nothing about the system: it imports kgq.answer.ask, the same entry
point the workbench uses, and records what comes back. No per-question logic,
no prompt edits, no retrieval tweaks -- J-2 §18.

Durability (harness amendment, execution only): every completed question is
persisted atomically before the next one starts, so an interrupted run resumes
instead of being lost. Nothing about what is measured changes.
"""
from __future__ import annotations

import argparse, hashlib, json, os, pathlib, sys, tempfile, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from kgc import SCHEMA_VERSION
from kgq import DEMO_VERSION
from kgq.answer import ANSWER, Budget, ask
from kgq.provider import Provider, ProviderError

ROOT = pathlib.Path(__file__).resolve().parents[2]
QUERIES = ROOT / "eval/j2/J2_QUERIES.json"
GOLD = ROOT / "eval/j2/J2_GOLD.json"
CORPUS = (ROOT / "eval/corpus3").resolve()
CORPUS_SHA = "19f4028a0a9f15b4e77d41ad489ee3368b72b309c9ac2836b1d34941fc32d35f"


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_atomic(path: pathlib.Path, payload: dict) -> None:
    """Write-then-rename so a kill can never leave a half-written checkpoint.

    os.replace is atomic within a filesystem, and the temp file is fsynced
    first, so the rename cannot publish bytes the kernel has not committed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


class PacedProvider:
    """Rate-limit pacing for the HARNESS, not the system.

    Groq's free tier allows 8,000 tokens per minute per model. A J-2 answer
    prompt carries up to 24,000 characters of evidence, so roughly one question
    per minute fits. Without pacing the provider returns HTTP 429, `ask()`
    correctly reports "model unavailable" and abstains -- which would measure
    Groq's billing tier, not the semantic layer.

    This wrapper only delays and retries transport. It does not alter messages,
    temperature, budgets, retrieval, validation or any decision the system
    makes. `Provider.chat`'s own accounting still records the real API time, so
    latency is reported from that and excludes every sleep here.
    """

    TPM = 8000
    HEADROOM = 0.92

    def __init__(self, inner, *, verbose: bool = True, paced_seconds: float = 0.0,
                 retries: int = 0):
        self.inner = inner
        self.verbose = verbose
        self._window: list[tuple[float, int]] = []
        self.paced_seconds = paced_seconds          # carried across a resume
        self.rate_limit_retries = retries

    base_url = property(lambda self: self.inner.base_url)
    model = property(lambda self: self.inner.model)
    usage = property(lambda self: self.inner.usage)

    def _spent_last_minute(self) -> int:
        now = time.time()
        self._window = [(t, n) for t, n in self._window if now - t < 60]
        return sum(n for _, n in self._window)

    def _wait_for_room(self, need: int) -> None:
        while self._window and self._spent_last_minute() + need > self.TPM * self.HEADROOM:
            oldest = self._window[0][0]
            nap = max(1.0, 61 - (time.time() - oldest))
            if self.verbose:
                print(f"        pacing {nap:.0f}s "
                      f"({self._spent_last_minute()}/{self.TPM} tpm)", flush=True)
            time.sleep(nap)
            self.paced_seconds += nap

    def chat(self, messages, **kw):
        # max_tokens is a ceiling, not a forecast: these replies run a few hundred
        # tokens. Over-estimating it costs a full 60s window on every call.
        estimate = (sum(len(m.get("content", "")) for m in messages) // 4
                    + kw.get("max_tokens", 1200) // 3)
        self._wait_for_room(estimate)
        for attempt in range(6):
            before = self.inner.usage.input_tokens + self.inner.usage.output_tokens
            try:
                out = self.inner.chat(messages, **kw)
                spent = (self.inner.usage.input_tokens + self.inner.usage.output_tokens) - before
                self._window.append((time.time(), spent if spent > 0 else estimate))
                return out
            except ProviderError as exc:
                if "429" not in str(exc) or attempt == 5:
                    raise
                self.rate_limit_retries += 1
                nap = 20.0 * (attempt + 1)
                if self.verbose:
                    print(f"        429; backing off {nap:.0f}s", flush=True)
                time.sleep(nap)
                self.paced_seconds += nap
                self._window.clear()
        raise AssertionError("unreachable")


def frozen_identity(run_label: str, provider, db: str, budget: Budget, qfile: pathlib.Path) -> dict:
    """Everything a resume must match before it may append to a checkpoint."""
    return {
        "run_label": run_label,
        "queries_file": str(qfile),
        "queries_sha256": sha(qfile),
        "gold_sha256": sha(GOLD),
        "corpus_content_sha256": CORPUS_SHA,
        "system_under_test": {
            "compiler_commit": json.loads((ROOT / "eval/j2/J2_SYSTEM_UNDER_TEST.json")
                                          .read_text())["compiler_commit"],
            "schema_version": SCHEMA_VERSION,
            "demo_version": DEMO_VERSION,
        },
        "provider": {
            "base_url": provider.base_url,
            "model": provider.model,
            "temperature": 0.0,                     # Provider.chat's default
            "budget": budget.as_dict(),
        },
        "database": db,
    }


def check_resumable(ckpt: dict, now: dict) -> list[str]:
    """Return the list of frozen inputs that differ. Empty list means resumable.

    The database file's own hash is deliberately NOT compared: the retriever
    builds an FTS index on first use, so the file legitimately changes during a
    run. The corpus hash is what pins the content.
    """
    was = ckpt["frozen"]
    bad = []
    for key in ("run_label", "queries_sha256", "gold_sha256", "corpus_content_sha256",
                "database", "queries_file"):
        if was.get(key) != now.get(key):
            bad.append(f"{key}: checkpoint {was.get(key)!r} != now {now.get(key)!r}")
    for key in ("base_url", "model", "temperature"):
        if was["provider"].get(key) != now["provider"].get(key):
            bad.append(f"provider.{key}: checkpoint {was['provider'].get(key)!r} "
                       f"!= now {now['provider'].get(key)!r}")
    if was["provider"].get("budget") != now["provider"].get("budget"):
        bad.append(f"provider.budget: checkpoint {was['provider'].get('budget')} "
                   f"!= now {now['provider'].get('budget')}")
    for key in ("compiler_commit", "schema_version", "demo_version"):
        if was["system_under_test"].get(key) != now["system_under_test"].get(key):
            bad.append(f"system_under_test.{key}: checkpoint "
                       f"{was['system_under_test'].get(key)!r} != "
                       f"now {now['system_under_test'].get(key)!r}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-label", required=True)
    ap.add_argument("--cache", default="")
    ap.add_argument("--queries", default="")
    ap.add_argument("--checkpoint", default="", help="defaults to <out>.ckpt.json")
    ap.add_argument("--resume", action="store_true",
                    help="continue an existing checkpoint instead of refusing")
    a = ap.parse_args()

    qfile = pathlib.Path(a.queries) if a.queries else QUERIES
    qs = json.loads(qfile.read_bytes())["questions"]
    ckpt_path = pathlib.Path(a.checkpoint or (a.out + ".ckpt.json"))
    budget = Budget()

    inner = Provider.from_env(cache_path=a.cache or None)
    now = frozen_identity(a.run_label, inner, a.db, budget, qfile)

    done: dict[str, dict] = {}
    carried = {"paced_seconds": 0.0, "retries": 0,
               "usage": {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0,
                         "cache_hits": 0, "seconds": 0.0}}
    if ckpt_path.exists():
        ckpt = json.loads(ckpt_path.read_text())
        if not a.resume:
            # never silently restart from question 1 over a real checkpoint
            print(f"REFUSING: a checkpoint already exists at {ckpt_path} with "
                  f"{len(ckpt.get('results', []))} completed question(s).\n"
                  f"Pass --resume to continue it, or delete it to start over.",
                  file=sys.stderr)
            return 2
        bad = check_resumable(ckpt, now)
        if bad:
            print("REFUSING TO RESUME: frozen inputs differ from the checkpoint.",
                  file=sys.stderr)
            for b in bad:
                print(f"  - {b}", file=sys.stderr)
            return 3
        done = {r["id"]: r for r in ckpt.get("results", [])}
        carried["paced_seconds"] = ckpt.get("harness_paced_seconds", 0.0)
        carried["retries"] = ckpt.get("harness_rate_limit_retries", 0)
        carried["usage"] = ckpt.get("cumulative_usage", carried["usage"])
        print(f"resuming {ckpt_path}: {len(done)} already complete, "
              f"{len(qs) - len(done)} remaining")

    provider = PacedProvider(inner, paced_seconds=carried["paced_seconds"],
                             retries=carried["retries"])
    t_all = time.perf_counter()

    def snapshot(extra: dict | None = None) -> dict:
        u = provider.usage.as_dict()
        cum = {k: carried["usage"].get(k, 0) + u.get(k, 0) for k in u}
        return {
            "run_label": a.run_label,
            "frozen": now,
            "completed_ids": [q["id"] for q in qs if q["id"] in done],
            "completed_count": len(done),
            "total_questions": len(qs),
            "results": [done[q["id"]] for q in qs if q["id"] in done],
            "cumulative_usage": cum,
            "harness_paced_seconds": round(provider.paced_seconds, 1),
            "harness_rate_limit_retries": provider.rate_limit_retries,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **(extra or {}),
        }

    pending = [q for q in qs if q["id"] not in done]
    for i, q in enumerate(pending, 1):
        t0 = time.perf_counter()
        u = provider.usage
        before = u.as_dict()
        try:
            r = ask(q["question"], db_path=a.db, corpus_root=str(CORPUS),
                    provider=provider, budget=budget)
            d = r.as_dict()
            d["is_answer"] = r.status == ANSWER
            d["error"] = None
        except Exception as exc:                 # a crash is a result, not a gap
            d = {"question": q["question"], "status": "RUNNER_ERROR",
                 "error": f"{type(exc).__name__}: {exc}", "is_answer": False}
        after = u.as_dict()
        d["id"] = q["id"]
        d["wall_seconds"] = round(time.perf_counter() - t0, 3)
        d["usage_delta"] = {k: after.get(k, 0) - before.get(k, 0) for k in after}
        done[q["id"]] = d
        write_atomic(ckpt_path, snapshot())       # durable BEFORE the next question
        print(f"  [{len(done):2}/{len(qs)}] {q['id']}  {d['status']:18} "
              f"{d['wall_seconds']:6.2f}s", flush=True)

    assert len(done) == len(qs), f"{len(done)} != {len(qs)}"
    ordered = [done[q["id"]] for q in qs]
    ids = [r["id"] for r in ordered]
    assert len(set(ids)) == len(ids), "duplicate question ids in the final artifact"

    final = snapshot({
        "database_sha256": sha(pathlib.Path(a.db)),
        "queries_sha256": now["queries_sha256"],
        "gold_sha256": now["gold_sha256"],
        "corpus_content_sha256": CORPUS_SHA,
        "provider": now["provider"] | {"cache": a.cache or "disabled"},
        "budget": budget.as_dict(),
        "database": a.db,
        "session_seconds": round(time.perf_counter() - t_all, 2),
        "results": ordered,
    })
    write_atomic(pathlib.Path(a.out), final)
    print(f"\nwrote {a.out}  ({len(ordered)} results, "
          f"{final['session_seconds']}s this session)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
