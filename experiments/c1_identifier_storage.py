"""C-1: identifier storage representation. TEXT(hex64) vs BLOB(16) vs BLOB(32).

Pure measurement. Does not touch kgc/ -- this is an experiment, not a migration.
"""
from __future__ import annotations
import hashlib, os, random, sqlite3, statistics, tempfile, time, json

random.seed(17)
N_SYM, N_CLAIM, N_EV = 20_000, 32_000, 32_000

def ids(n, prefix):
    return [hashlib.sha256(f"{prefix}{i}".encode()).digest() for i in range(n)]

SCHEMAS = {
 "TEXT_hex64": ("TEXT", lambda b: b.hex()),            # current representation
 "TEXT_hex32": ("TEXT", lambda b: b[:16].hex()),       # 128-bit, still readable
 "BLOB_32":    ("BLOB", lambda b: b),
 "BLOB_16":    ("BLOB", lambda b: b[:16]),             # 128-bit, opaque
}

DDL = """
PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;
CREATE TABLE symbol(symbol_id {T} PRIMARY KEY, artifact_id {T}, parent_id {T},
                    qualified_name TEXT, kind TEXT, locator TEXT);
CREATE TABLE evidence(evidence_id {T} PRIMARY KEY, artifact_id {T}, artifact_sha256 {T},
                      locator TEXT, quoted_text TEXT, verification_strength TEXT);
CREATE TABLE claim(claim_id {T} PRIMARY KEY, predicate TEXT, subject_id {T}, object_id {T},
                   establishment TEXT, lifecycle TEXT, run_id {T}, evidence_ids TEXT);
CREATE TABLE claim_evidence(claim_id {T}, evidence_id {T}, PRIMARY KEY(claim_id, evidence_id));
CREATE INDEX i_ce_ev ON claim_evidence(evidence_id);
CREATE INDEX i_claim_subject ON claim(subject_id, predicate);
CREATE INDEX i_symbol_qn ON symbol(qualified_name);
CREATE INDEX i_symbol_art ON symbol(artifact_id);
"""

def build(name):
    typ, conv = SCHEMAS[name]
    path = tempfile.mktemp(suffix=".db")
    con = sqlite3.connect(path, isolation_level=None)
    con.executescript(DDL.replace("{T}", typ))
    sym, ev, cl = ids(N_SYM, "s"), ids(N_EV, "e"), ids(N_CLAIM, "c")
    art = ids(200, "a"); run = conv(ids(1, "r")[0])

    t0 = time.perf_counter()
    con.execute("BEGIN")
    con.executemany("INSERT INTO symbol VALUES(?,?,?,?,?,?)",
        [(conv(sym[i]), conv(art[i % 200]), conv(sym[max(0, i - 1)]),
          f"pkg.mod{i//50}.sym{i}", "function",
          '{"byte_start":%d,"byte_end":%d,"line_start":1,"line_end":2}' % (i, i + 40))
         for i in range(N_SYM)])
    con.executemany("INSERT INTO evidence VALUES(?,?,?,?,?,?)",
        [(conv(ev[i]), conv(art[i % 200]), conv(art[i % 200]),
          '{"byte_start":%d,"byte_end":%d}' % (i, i + 40), "some quoted source text here",
          "EXACT") for i in range(N_EV)])
    con.executemany("INSERT INTO claim VALUES(?,?,?,?,?,?,?,?)",
        [(conv(cl[i]), "CALLS", conv(sym[i % N_SYM]), conv(sym[(i * 7) % N_SYM]),
          "DERIVED", "ACTIVE", run, json.dumps([conv(ev[i]).hex() if typ == "BLOB" else conv(ev[i])]))
         for i in range(N_CLAIM)])
    con.executemany("INSERT INTO claim_evidence VALUES(?,?)",
        [(conv(cl[i]), conv(ev[i])) for i in range(N_CLAIM)])
    con.execute("COMMIT")
    insert_s = time.perf_counter() - t0
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    sizes = {}
    for r in con.execute("SELECT name, sum(pgsize) FROM dbstat GROUP BY name"):
        sizes[r[0]] = r[1]
    idx_bytes = sum(v for k, v in sizes.items()
                    if (con.execute("SELECT type FROM sqlite_master WHERE name=?", (k,)).fetchone() or ["?"])[0] == "index")
    total = os.path.getsize(path)

    # lookup latency: PK lookup + join + subject scan
    probes = [conv(sym[random.randrange(N_SYM)]) for _ in range(2000)]
    t = []
    for p in probes:
        t0 = time.perf_counter(); con.execute("SELECT qualified_name FROM symbol WHERE symbol_id=?", (p,)).fetchone(); t.append((time.perf_counter()-t0)*1e6)
    pk_us = statistics.median(t)
    t = []
    for p in probes[:500]:
        t0 = time.perf_counter()
        con.execute("SELECT c.claim_id FROM claim c WHERE c.subject_id=?", (p,)).fetchall()
        t.append((time.perf_counter()-t0)*1e6)
    subj_us = statistics.median(t)
    t0 = time.perf_counter()
    con.execute("SELECT count(*) FROM evidence e WHERE NOT EXISTS (SELECT 1 FROM claim_evidence ce WHERE ce.evidence_id=e.evidence_id)").fetchone()
    audit_ms = (time.perf_counter()-t0)*1e3
    con.close(); os.unlink(path)
    for s in (path+"-wal", path+"-shm"):
        if os.path.exists(s): os.unlink(s)
    return {"repr": name, "db_mb": round(total/1e6, 2), "index_mb": round(idx_bytes/1e6, 2),
            "index_pct": round(100*idx_bytes/total, 1), "insert_s": round(insert_s, 2),
            "rows_per_s": round((N_SYM+N_EV+N_CLAIM*2)/insert_s), "pk_lookup_us": round(pk_us, 1),
            "subject_scan_us": round(subj_us, 1), "audit_ms": round(audit_ms, 1)}

if __name__ == "__main__":
    out = [build(k) for k in SCHEMAS]
    base = out[0]
    for r in out:
        r["db_vs_text"] = round(r["db_mb"]/base["db_mb"], 3)
    print(json.dumps(out, indent=2))
