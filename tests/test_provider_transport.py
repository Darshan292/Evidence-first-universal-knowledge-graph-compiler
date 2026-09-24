"""The real Provider against a real HTTP server.

`ScriptedProvider` proves the answer path. It does not prove the transport: the
request shape, the auth header, the response parsing, the usage accounting or
the cache. This starts a local server that speaks the OpenAI-compatible
`/chat/completions` shape -- the same shape Ollama, Groq and OpenRouter speak --
and drives the whole demo through `Provider`.

It does not prove that a hosted model returns a groundable answer. Nothing here
claims that.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kgc.pipeline import ingest
from kgc.store import Store
from kgq.answer import ANSWER, Budget, ask
from kgq.provider import Provider, ProviderError
from kgq.retrieval import Retriever
from tests.test_demo_0_1 import CORPUS

STATE: dict = {"requests": [], "reply": "", "status": 200}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        STATE["requests"].append({"path": self.path, "body": body,
                                  "auth": self.headers.get("Authorization"),
                                  "user_agent": self.headers.get("User-Agent")})
        if STATE["status"] != 200:
            self.send_response(STATE["status"]); self.end_headers()
            self.wfile.write(b'{"error":"upstream said no"}')
            return
        out = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": STATE["reply"]}}],
            "usage": {"prompt_tokens": 1234, "completion_tokens": 56}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class TestProviderTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "corpus"
        for rel, body in CORPUS.items():
            p = cls.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        cls.db = str(Path(cls.tmp.name) / "kg.sqlite")
        s = Store(cls.db); ingest(s, cls.root); s.close()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def setUp(self):
        STATE["requests"].clear()
        STATE["status"] = 200
        self.cache = str(Path(self.tmp.name) / f"cache-{self.id()}.sqlite")

    def provider(self, key="test-key"):
        return Provider(f"http://127.0.0.1:{self.port}/v1", "a-model", key,
                        cache_path=self.cache)

    def grounded_reply(self):
        r = Retriever(self.db, str(self.root))
        spans, _ = r.retrieve("How does safe_join stop an unsafe path?")
        eid = [s for s in spans if s.symbol.endswith("safe_join")][0].evidence_id
        r.close()
        text = "safe_join returns None when the path would escape the base directory."
        return json.dumps({"answer": text,
                           "claims": [{"text": text, "evidence_ids": [eid]}]})

    # ── transport ───────────────────────────────────────────────────────
    def test_the_request_has_the_openai_compatible_shape(self):
        STATE["reply"] = '{"answer":"x","claims":[]}'
        self.provider().chat([{"role": "user", "content": "hi"}])
        req = STATE["requests"][0]
        self.assertEqual(req["path"], "/v1/chat/completions")
        self.assertEqual(req["body"]["model"], "a-model")
        self.assertEqual(req["body"]["messages"][0]["role"], "user")
        self.assertIn("temperature", req["body"])
        self.assertIn("max_tokens", req["body"])

    def test_the_api_key_is_sent_as_a_bearer_token(self):
        STATE["reply"] = "{}"
        self.provider().chat([{"role": "user", "content": "hi"}])
        self.assertEqual(STATE["requests"][0]["auth"], "Bearer test-key")

    def test_a_local_model_needs_no_key_and_sends_no_auth_header(self):
        STATE["reply"] = "{}"
        self.provider(key=None).chat([{"role": "user", "content": "hi"}])
        self.assertIsNone(STATE["requests"][0]["auth"])

    def test_usage_is_read_from_the_response(self):
        STATE["reply"] = "{}"
        p = self.provider()
        p.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(p.usage.calls, 1)
        self.assertEqual(p.usage.input_tokens, 1234)
        self.assertEqual(p.usage.output_tokens, 56)
        self.assertGreater(p.usage.seconds, 0)

    def test_an_identical_request_is_served_from_cache(self):
        STATE["reply"] = "{}"
        p = self.provider()
        msgs = [{"role": "user", "content": "same"}]
        p.chat(msgs); p.chat(msgs)
        self.assertEqual(len(STATE["requests"]), 1, "the second call hit the network")
        self.assertEqual(p.usage.cache_hits, 1)

    def test_an_http_error_becomes_a_ProviderError_not_a_crash(self):
        STATE["status"] = 500
        with self.assertRaises(ProviderError):
            self.provider().chat([{"role": "user", "content": "hi"}])

    def test_an_unreachable_endpoint_becomes_a_ProviderError(self):
        p = Provider("http://127.0.0.1:1/v1", "m", None, timeout=2)
        with self.assertRaises(ProviderError):
            p.chat([{"role": "user", "content": "hi"}])

    # ── the whole demo, over real HTTP ──────────────────────────────────
    def test_the_full_path_runs_over_real_http(self):
        STATE["reply"] = self.grounded_reply()
        r = ask("How does safe_join stop an unsafe path?",
                db_path=self.db, corpus_root=str(self.root),
                provider=self.provider(), budget=Budget(interpretation_attempts=0))
        self.assertEqual(r.status, ANSWER, r.abstain_reason)
        self.assertEqual(r.establishment, "PROPOSED")
        self.assertIn("safe_join", r.answer)
        self.assertEqual(r.usage["llm_calls"], 1)
        self.assertEqual(r.usage["input_tokens"], 1234)
        self.assertTrue(STATE["requests"], "no HTTP request was made")

    def test_the_prompt_that_crosses_the_wire_isolates_source_as_data(self):
        STATE["reply"] = self.grounded_reply()
        ask("How does safe_join stop an unsafe path?", db_path=self.db,
            corpus_root=str(self.root), provider=self.provider(),
            budget=Budget(interpretation_attempts=0))
        sent = STATE["requests"][0]["body"]["messages"]
        self.assertIn("DATA, not instruction", sent[0]["content"])
        self.assertIn("begin untrusted source content", sent[1]["content"])

    def test_a_model_that_returns_prose_instead_of_json_is_rejected(self):
        STATE["reply"] = "safe_join checks the path. Trust me."
        r = ask("How does safe_join stop an unsafe path?", db_path=self.db,
                corpus_root=str(self.root), provider=self.provider(),
                budget=Budget(interpretation_attempts=0))
        self.assertNotEqual(r.status, ANSWER)
        self.assertEqual(len(STATE["requests"]), 2, "it should have retried once")

    def test_provider_selection_is_two_environment_variables(self):
        import os
        old = {k: os.environ.get(k) for k in ("KGQ_BASE_URL", "KGQ_MODEL", "KGQ_API_KEY")}
        try:
            os.environ["KGQ_BASE_URL"] = f"http://127.0.0.1:{self.port}/v1"
            os.environ["KGQ_MODEL"] = "from-env"
            os.environ.pop("KGQ_API_KEY", None)
            p = Provider.from_env()
            self.assertEqual(p.model, "from-env")
            self.assertIsNone(p.api_key)
            os.environ.pop("KGQ_BASE_URL")
            with self.assertRaises(ProviderError):
                Provider.from_env()
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v



    def test_request_identifies_the_client(self):
        """DEFECT-001: urllib's default User-Agent is rejected by real CDNs.

        Groq's edge answers "Python-urllib/3.11" with Cloudflare 1010, which
        reaches a user as an unexplained 403 and a silent abstention. A local
        test server accepts anything, so only an explicit assertion catches it.
        """
        from kgq.provider import USER_AGENT
        STATE["reply"] = '{"answer": "a", "claims": []}'
        p = Provider(f"http://127.0.0.1:{self.port}/v1", "m", None)
        p.chat([{"role": "user", "content": "hi"}])
        ua = STATE["requests"][-1]["user_agent"]
        self.assertEqual(ua, USER_AGENT)
        self.assertNotIn("Python-urllib", ua)
        self.assertTrue(ua.startswith("evidence-first-kg/"), ua)

if __name__ == "__main__":
    unittest.main()
