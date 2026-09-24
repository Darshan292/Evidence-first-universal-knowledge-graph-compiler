"""HARNESS TESTING ONLY -- this produces NO J-2 result.

Proves the J-2 runner's checkpoint/resume mechanics against a deterministic
mock transport. The mock is a language model in no sense whatever; its replies
are fixed strings. Nothing here measures the semantic layer, and its output
must never be reported as, or merged into, a J-2 run.

What it proves:
  1. a checkpoint is written after every completed question
  2. SIGKILL mid-run leaves a VALID checkpoint (atomic write)
  3. resuming skips completed ids and does not re-ask them
  4. resuming does not alter any completed result
  5. the final artifact holds exactly 48 results, each id exactly once
  6. running over an existing checkpoint WITHOUT --resume refuses (exit 2)
  7. resuming with a changed frozen input refuses (exit 3)
"""
from __future__ import annotations

import json, os, pathlib, shutil, signal, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRATCH = pathlib.Path(os.environ.get("HARNESS_SCRATCH", "/tmp/j2_harness_test"))
REPLY = json.dumps({"answer": "mock", "claims": [
    {"text": "mock statement", "evidence_ids": [], "quote": ""}]})


class Mock(BaseHTTPRequestHandler):
    def do_POST(self):
        _ = self.rfile.read(int(self.headers["Content-Length"]))
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": REPLY}}],
                          "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def run(args, env, **kw):
    return subprocess.run([sys.executable, str(ROOT / "eval/j2/run_j2.py"), *args],
                          cwd=str(ROOT), env=env, capture_output=True, text=True, **kw)


def main() -> int:
    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)
    db = SCRATCH / "harness.sqlite"
    shutil.copy(ROOT / "eval/j2/j2_run1.sqlite", db)
    out = SCRATCH / "HARNESS_TEST_NOT_A_J2_RESULT.json"
    ckpt = SCRATCH / "HARNESS_TEST.ckpt.json"

    srv = HTTPServer(("127.0.0.1", 0), Mock)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    env = dict(os.environ)
    env.update({"KGQ_BASE_URL": f"http://127.0.0.1:{port}/v1",
                "KGQ_MODEL": "mock-harness-model", "KGQ_API_KEY": ""})
    base = ["--db", str(db), "--out", str(out), "--run-label", "harness-test",
            "--checkpoint", str(ckpt)]
    ok = True

    def check(label, cond, detail=""):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' -- ' + detail) if detail and not cond else ''}")
        ok = ok and cond

    # ---- 1-2: kill after 3 questions, checkpoint must survive intact --------
    print("\n[1] run, SIGKILL after 3 questions")
    p = subprocess.Popen([sys.executable, str(ROOT / "eval/j2/run_j2.py"), *base],
                         cwd=str(ROOT), env=env, stdout=subprocess.PIPE, text=True)
    deadline = time.time() + 180
    snap = None
    while time.time() < deadline:
        if ckpt.exists():
            try:
                snap = json.loads(ckpt.read_text())
            except json.JSONDecodeError:
                snap = None                      # would mean a torn write
            if snap and snap["completed_count"] >= 3:
                break
        time.sleep(0.05)
    os.kill(p.pid, signal.SIGKILL)
    p.wait()
    check("checkpoint exists after kill", ckpt.exists())
    reloaded = json.loads(ckpt.read_text())      # raises if the write was torn
    check("checkpoint is valid JSON after SIGKILL", True)
    n_before = reloaded["completed_count"]
    check("checkpoint holds >=3 completed questions", n_before >= 3, str(n_before))
    check("results length matches completed_count", len(reloaded["results"]) == n_before)
    for field in ("queries_sha256", "gold_sha256", "corpus_content_sha256",
                  "run_label", "provider", "system_under_test", "database"):
        check(f"checkpoint records frozen.{field}", field in reloaded["frozen"])
    for field in ("cumulative_usage", "harness_paced_seconds",
                  "harness_rate_limit_retries", "timestamp", "completed_ids"):
        check(f"checkpoint records {field}", field in reloaded)
    first_three = {r["id"]: r for r in reloaded["results"][:3]}

    # ---- 6: refuse to silently restart -------------------------------------
    print("\n[2] running again WITHOUT --resume must refuse")
    r = run(base, env)
    check("exit code 2", r.returncode == 2, f"got {r.returncode}")
    check("says a checkpoint exists", "REFUSING" in r.stderr, r.stderr[:120])
    check("did not overwrite the checkpoint",
          json.loads(ckpt.read_text())["completed_count"] == n_before)

    # ---- 7: refuse to resume across a changed frozen input -----------------
    print("\n[3] --resume with a different run label must refuse")
    r = run(["--db", str(db), "--out", str(out), "--run-label", "DIFFERENT",
             "--checkpoint", str(ckpt), "--resume"], env)
    check("exit code 3", r.returncode == 3, f"got {r.returncode}")
    check("names the mismatched field", "run_label" in r.stderr, r.stderr[:160])

    print("\n[4] --resume with a different model must refuse")
    env2 = dict(env); env2["KGQ_MODEL"] = "some-other-model"
    r = run(base + ["--resume"], env2)
    check("exit code 3", r.returncode == 3, f"got {r.returncode}")
    check("names provider.model", "provider.model" in r.stderr, r.stderr[:160])

    # ---- 3-5: resume and finish -------------------------------------------
    print("\n[5] resume and complete")
    r = run(base + ["--resume"], env)
    check("exit code 0", r.returncode == 0, r.stderr[-300:])
    check("announced the resume", f"{n_before} already complete" in r.stdout,
          r.stdout[:160])
    asked = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("[")]
    check("did not re-ask completed questions", len(asked) == 48 - n_before,
          f"asked {len(asked)}, expected {48 - n_before}")

    final = json.loads(out.read_text())
    ids = [x["id"] for x in final["results"]]
    check("final holds exactly 48 results", len(final["results"]) == 48, str(len(ids)))
    check("every id appears exactly once", len(set(ids)) == 48)
    check("ids are in the frozen query order",
          ids == [q["id"] for q in json.loads((ROOT / "eval/j2/J2_QUERIES.json").read_text())["questions"]])
    check("no result altered by the resume",
          all(final["results"][i] == first_three[final["results"][i]["id"]]
              for i in range(3) if final["results"][i]["id"] in first_three))
    check("final carries the frozen hashes",
          final["queries_sha256"] == reloaded["frozen"]["queries_sha256"]
          and final["gold_sha256"] == reloaded["frozen"]["gold_sha256"])

    srv.shutdown()
    print(f"\n{'ALL HARNESS CHECKS PASSED' if ok else 'HARNESS CHECKS FAILED'}")
    print("This was HARNESS TESTING against a mock transport. It is NOT a J-2 result.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
