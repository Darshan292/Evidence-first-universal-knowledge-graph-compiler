"""J-2 runner. Executes the FROZEN system over the FROZEN query set.

It changes nothing about the system: it imports `kgq.answer.ask`, the same entry
point the workbench uses, and records what comes back. No per-question logic, no
prompt edits, no retrieval tweaks (§18).

Two harness-side concerns only, both explicitly authorised:

  RATE LIMITING   Groq's tier allows 8,000 tokens per minute per model. An
                  unpaced run gets HTTP 429, `ask()` correctly reports "model
                  unavailable" and abstains -- which measures a billing tier,
                  not a semantic layer. `PacedProvider` only delays and retries
                  transport.

  MEASUREMENT     Per-request token counts are read off the WIRE, from the
                  provider's own `usage` block, never estimated from character
                  counts. This is done by wrapping `urllib.request.urlopen`
                  inside this process, so `kgq/provider.py` stays byte-identical.

Results are written after every question, so an interruption cannot destroy
completed work, and `--resume` continues a partial file.
"""
from __future__ import annotations

import argparse, hashlib, json, os, pathlib, sys, time, urllib.error, urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from kgq.answer import ANSWER, Budget, ask
from kgq.provider import Provider, ProviderError

ROOT = pathlib.Path(__file__).resolve().parents[2]
QUERIES = ROOT / "eval/j2/J2_QUERIES.json"
CORPUS = (ROOT / "eval/corpus3").resolve()


# ── wire-level measurement ──────────────────────────────────────────────
class Wire:
    """Observes every HTTP call the frozen provider makes. Measurement only.

    It records the provider's own reported usage -- prompt_tokens,
    completion_tokens, total_tokens, and any cache field the provider chooses to
    expose -- rather than estimating from characters. It never alters a request,
    a response, or a decision.
    """

    def __init__(self):
        self.calls: list[dict] = []
        self.http_429 = 0
        self.http_other_errors = 0
        self._orig = urllib.request.urlopen

    def install(self):
        wire = self

        class _Replay:
            def __init__(self, body, status):
                self._body, self.status = body, status
            def read(self, *a): return self._body
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def patched(req, *a, **kw):
            t0 = time.perf_counter()
            try:
                resp = wire._orig(req, *a, **kw)
                body = resp.read()
                status = getattr(resp, "status", 200)
                try:
                    resp.close()
                except Exception:
                    pass
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    wire.http_429 += 1
                else:
                    wire.http_other_errors += 1
                wire.calls.append({"http_status": exc.code,
                                   "seconds": round(time.perf_counter() - t0, 3)})
                raise
            elapsed = round(time.perf_counter() - t0, 3)
            rec = {"http_status": status, "seconds": elapsed}
            try:
                u = (json.loads(body.decode()) or {}).get("usage") or {}
                rec["input_tokens"] = u.get("prompt_tokens")
                rec["output_tokens"] = u.get("completion_tokens")
                rec["total_tokens"] = u.get("total_tokens")
                # §4: recorded only if the provider exposes it. Nothing is done
                # to the prompts to induce caching.
                details = u.get("prompt_tokens_details") or {}
                cached = details.get("cached_tokens", u.get("cached_tokens"))
                rec["cached_tokens"] = cached
                rec["cache_hit"] = bool(cached) if cached is not None else None
                cd = u.get("completion_tokens_details") or {}
                if "reasoning_tokens" in cd:
                    rec["reasoning_tokens"] = cd["reasoning_tokens"]
                for k in ("queue_time", "prompt_time", "completion_time", "total_time"):
                    if k in u:
                        rec[k] = u[k]
            except Exception:
                pass
            wire.calls.append(rec)
            return _Replay(body, status)

        urllib.request.urlopen = patched
        return self

    def drain(self) -> list[dict]:
        out, self.calls = self.calls, []
        return out


class PacedProvider:
    """Rate-limit pacing for the HARNESS, not the system.

    Only delays and retries transport. It does not alter messages, temperature,
    budgets, retrieval, validation or any decision the system makes.
    `Provider.chat`'s own accounting still records the real API time, so latency
    is reported from that and excludes every sleep here.
    """

    TPM = 8000
    HEADROOM = 0.92

    def __init__(self, inner, *, verbose: bool = True):
        self.inner = inner
        self.verbose = verbose
        self._window: list[tuple[float, int]] = []
        self.paced_seconds = 0.0
        self.rate_limit_retries = 0

    base_url = property(lambda self: self.inner.base_url)
    model = property(lambda self: self.inner.model)
    usage = property(lambda self: self.inner.usage)

    def _spent(self) -> int:
        now = time.time()
        self._window = [(t, n) for t, n in self._window if now - t < 60]
        return sum(n for _, n in self._window)

    def _wait(self, need: int) -> None:
        while self._window and self._spent() + need > self.TPM * self.HEADROOM:
            nap = max(1.0, 61 - (time.time() - self._window[0][0]))
            if self.verbose:
                print(f"        pacing {nap:.0f}s ({self._spent()}/{self.TPM} tpm)", flush=True)
            time.sleep(nap)
            self.paced_seconds += nap

    def chat(self, messages, **kw):
        # max_tokens is a ceiling, not a forecast
        estimate = (sum(len(m.get("content", "")) for m in messages) // 4
                    + kw.get("max_tokens", 1200) // 3)
        self._wait(estimate)
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


def write_atomic(path: pathlib.Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-label", required=True)
    ap.add_argument("--cache", default="")
    ap.add_argument("--queries", default="")
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()

    qfile = pathlib.Path(a.queries) if a.queries else QUERIES
    qraw = qfile.read_bytes()
    qs = json.loads(qraw)["questions"]
    out_path = pathlib.Path(a.out)

    done: dict[str, dict] = {}
    if a.resume and out_path.is_file():
        prev = json.loads(out_path.read_text())
        done = {r["id"]: r for r in prev.get("results", [])}
        print(f"resuming: {len(done)} of {len(qs)} already recorded")

    wire = Wire().install()
    provider = PacedProvider(Provider.from_env(cache_path=a.cache or None))

    header = {
        "run_label": a.run_label,
        "queries_file": str(qfile),
        "queries_sha256": hashlib.sha256(qraw).hexdigest(),
        "gold_sha256": hashlib.sha256((ROOT / "eval/j2/J2_GOLD.json").read_bytes()).hexdigest(),
        "corpus_content_sha256": "19f4028a0a9f15b4e77d41ad489ee3368b72b309c9ac2836b1d34941fc32d35f",
        "database": a.db,
        "database_sha256": hashlib.sha256(pathlib.Path(a.db).read_bytes()).hexdigest(),
        "provider": {"base_url": provider.base_url, "model": provider.model,
                     "temperature": 0.0, "max_tokens_per_call": 1200,
                     "endpoint_type": "OpenAI-compatible /chat/completions",
                     "cache": a.cache or "disabled"},
        "budget": Budget().as_dict(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    results = [done[q["id"]] for q in qs if q["id"] in done]
    t_all = time.perf_counter()
    for i, q in enumerate(qs, 1):
        if q["id"] in done:
            print(f"  [{i:2}/{len(qs)}] {q['id']}  (already recorded)", flush=True)
            continue
        wire.drain()
        t0 = time.perf_counter()
        try:
            r = ask(q["question"], db_path=a.db, corpus_root=str(CORPUS),
                    provider=provider, budget=Budget())
            d = r.as_dict()
            d["is_answer"] = r.status == ANSWER
            d["error"] = None
        except Exception as exc:                 # a crash is a result, not a gap
            d = {"question": q["question"], "status": "RUNNER_ERROR",
                 "error": f"{type(exc).__name__}: {exc}", "is_answer": False}
        reqs = wire.drain()
        d["id"] = q["id"]
        d["wall_seconds"] = round(time.perf_counter() - t0, 3)
        d["requests"] = reqs                     # actual per-request wire usage
        d["measured"] = {
            "llm_calls": len([x for x in reqs if x.get("http_status") == 200]),
            "input_tokens": sum(x.get("input_tokens") or 0 for x in reqs),
            "output_tokens": sum(x.get("output_tokens") or 0 for x in reqs),
            "total_tokens": sum(x.get("total_tokens") or 0 for x in reqs),
            "cached_tokens": sum(x.get("cached_tokens") or 0 for x in reqs),
            "cache_hit": any(x.get("cache_hit") for x in reqs),
            "reasoning_tokens": sum(x.get("reasoning_tokens") or 0 for x in reqs),
            "http_429": len([x for x in reqs if x.get("http_status") == 429]),
            "api_seconds": round(sum(x.get("seconds") or 0 for x in reqs), 3),
        }
        results.append(d)
        m = d["measured"]
        print(f"  [{i:2}/{len(qs)}] {q['id']}  {d['status']:18} "
              f"calls={m['llm_calls']} in={m['input_tokens']:>5} out={m['output_tokens']:>4} "
              f"api={m['api_seconds']:5.2f}s wall={d['wall_seconds']:6.2f}s", flush=True)

        payload = dict(header)
        payload.update({
            "complete": len(results) == len(qs),
            "completed_count": len(results),
            "harness_paced_seconds": round(provider.paced_seconds, 1),
            "harness_rate_limit_retries": provider.rate_limit_retries,
            "http_429_total": wire.http_429,
            "http_other_errors": wire.http_other_errors,
            "elapsed_seconds": round(time.perf_counter() - t_all, 2),
            "results": results,
        })
        write_atomic(out_path, payload)          # persist after EVERY question

    print(f"\nwrote {a.out}  ({len(results)}/{len(qs)} recorded, "
          f"{round(time.perf_counter() - t_all, 1)}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
