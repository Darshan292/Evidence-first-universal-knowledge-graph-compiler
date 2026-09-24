"""J-2 runner. Executes the FROZEN system over the FROZEN query set.

It changes nothing about the system: it imports kgq.answer.ask, the same entry
point the workbench uses, and records what comes back. No per-question logic,
no prompt edits, no retrieval tweaks -- §18.
"""
from __future__ import annotations

import argparse, hashlib, json, os, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from kgq.answer import ANSWER, Budget, ask
from kgq.provider import Provider, ProviderError

ROOT = pathlib.Path(__file__).resolve().parents[2]
QUERIES = ROOT / "eval/j2/J2_QUERIES.json"
CORPUS = (ROOT / "eval/corpus3").resolve()


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
    HEADROOM = 0.92          # leave room for the reply we cannot measure first

    def __init__(self, inner, *, verbose: bool = True):
        self.inner = inner
        self.verbose = verbose
        self._window: list[tuple[float, int]] = []   # (timestamp, tokens)
        self.paced_seconds = 0.0
        self.rate_limit_retries = 0

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-label", required=True)
    ap.add_argument("--cache", default="")
    ap.add_argument("--queries", default="")
    a = ap.parse_args()

    qfile = pathlib.Path(a.queries) if a.queries else QUERIES
    qraw = qfile.read_bytes()
    qs = json.loads(qraw)["questions"]
    provider = PacedProvider(Provider.from_env(cache_path=a.cache or None))

    results = []
    t_all = time.perf_counter()
    for i, q in enumerate(qs, 1):
        t0 = time.perf_counter()
        u = getattr(provider, "usage", None)
        before = u.as_dict() if u is not None else {}
        try:
            r = ask(q["question"], db_path=a.db, corpus_root=str(CORPUS),
                    provider=provider, budget=Budget())
            d = r.as_dict()
            d["is_answer"] = r.status == ANSWER
            d["error"] = None
        except Exception as exc:                 # a crash is a result, not a gap
            d = {"question": q["question"], "status": "RUNNER_ERROR",
                 "error": f"{type(exc).__name__}: {exc}", "is_answer": False}
        after = u.as_dict() if u is not None else {}
        d["id"] = q["id"]
        d["wall_seconds"] = round(time.perf_counter() - t0, 3)
        d["usage_delta"] = {k: after.get(k, 0) - before.get(k, 0)
                            for k in set(after) | set(before)}
        results.append(d)
        print(f"  [{i:2}/{len(qs)}] {q['id']}  {d['status']:18} {d['wall_seconds']:6.2f}s",
              flush=True)

    out = {
        "run_label": a.run_label,
        "queries_file": str(qfile),
        "queries_sha256": hashlib.sha256(qraw).hexdigest(),
        "gold_sha256": hashlib.sha256((ROOT / "eval/j2/J2_GOLD.json").read_bytes()).hexdigest(),
        "corpus_content_sha256": "19f4028a0a9f15b4e77d41ad489ee3368b72b309c9ac2836b1d34941fc32d35f",
        "database": a.db,
        "database_sha256": hashlib.sha256(pathlib.Path(a.db).read_bytes()).hexdigest(),
        "harness_paced_seconds": round(provider.paced_seconds, 1),
        "harness_rate_limit_retries": provider.rate_limit_retries,
        "provider": {"base_url": provider.base_url, "model": provider.model,
                     "temperature": os.environ.get("KGQ_TEMPERATURE", "provider default"),
                     "cache": a.cache or "disabled"},
        "budget": Budget().as_dict(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time())),
        "total_seconds": round(time.perf_counter() - t_all, 2),
        "results": results,
    }
    pathlib.Path(a.out).write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nwrote {a.out}  ({out['total_seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
