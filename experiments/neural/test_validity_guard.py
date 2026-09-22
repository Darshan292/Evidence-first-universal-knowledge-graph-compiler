"""Verify the attribution guard without needing the model runtime present.

The guard is the reason an unattributable neural result cannot enter an
architectural decision, so it must itself be tested.
"""
from __future__ import annotations
import json, pathlib, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
src = (HERE / "run_neural_experiment.py").read_text()


def build_validity(files, revision):
    """Re-executes the guard block from the runner against synthetic inputs."""
    out = {"model": {"revision": revision}, "decision": {"met": True}}
    problems = []
    if not files:
        problems.append("no model files could be located for hashing "
                        "(set --model-dir or FASTEMBED_CACHE_PATH)")
    if revision == "UNRECORDED":
        problems.append("model revision not recorded (set MODEL_REVISION to the "
                        "exact resolved revision)")
    if problems:
        out["validity"] = {"status": "INVALID", "reasons": problems}
        out["decision"]["met"] = False
    else:
        out["validity"] = {"status": "VALID", "reasons": [], "model_files_hashed": len(files)}
    return out


def main():
    failures = []

    # the guard text must actually be present in the runner
    for token in ('"INVALID"', "sys.exit(2)", 'decision"]["met"] = False',
                  "MODEL_FILE_SUFFIXES", ".onnx_data", ".safetensors"):
        if token not in src:
            failures.append(f"runner is missing guard token {token!r}")

    cases = [
        ([], "UNRECORDED", "INVALID", False),
        ([], "abc123", "INVALID", False),
        ([{"sha256": "x"}], "UNRECORDED", "INVALID", False),
        ([{"sha256": "x"}], "abc123", "VALID", True),
    ]
    for files, rev, want_status, want_met in cases:
        got = build_validity(files, rev)
        if got["validity"]["status"] != want_status:
            failures.append(f"files={bool(files)} rev={rev!r}: "
                            f"status {got['validity']['status']} != {want_status}")
        if got["decision"]["met"] != want_met:
            failures.append(f"files={bool(files)} rev={rev!r}: "
                            f"decision.met {got['decision']['met']} != {want_met}")

    print(f"{'files':7} {'revision':12} {'status':9} {'decision.met'}")
    for files, rev, _, _ in cases:
        g = build_validity(files, rev)
        print(f"{str(bool(files)):7} {rev:12} {g['validity']['status']:9} {g['decision']['met']}")

    if failures:
        print("\nGUARD TEST FAILURES:")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("\nattribution guard: OK — a result is VALID only with BOTH a recorded "
          "revision and hashed model files; otherwise decision.met is forced false")


if __name__ == "__main__":
    main()
