# J-2 BLOCKED — no real model endpoint available

Status: **BLOCKED at the network layer.** No evaluation was run. No scripted
provider was substituted. J-2 is not complete and nothing here should be read
as a J-2 result.

## The block

Every OpenAI-compatible model host is refused by this environment's egress
proxy at the CONNECT stage, before TLS and before any request is sent:

```
> CONNECT api.groq.com:443 HTTP/1.1
< HTTP/1.1 403 Forbidden
curl: (56) CONNECT tunnel failed, response 403
```

Tested 2026-09-24, all refused:

| Host | Result |
|---|---|
| `api.groq.com` | CONNECT 403 |
| `openrouter.ai` | CONNECT 403 |
| `api.openai.com` | CONNECT 403 |
| `api.deepseek.com`, `api.mistral.ai`, `api.together.xyz`, `api.cerebras.ai`, `api.fireworks.ai`, `api.novita.ai`, `api.sambanova.ai`, `api.hyperbolic.xyz`, `api.perplexity.ai`, `api.x.ai`, `api.moonshot.cn`, `dashscope.aliyuncs.com` | CONNECT 403 |
| `ollama.com`, `registry.ollama.ai` | CONNECT 403 |
| `huggingface.co`, `cdn-lfs.huggingface.co`, `hf-mirror.com`, `modelscope.cn` | CONNECT 403 |
| `codeload.github.com` | CONNECT 403 |

Reachable: `pypi.org`, `files.pythonhosted.org`, `github.com`,
`raw.githubusercontent.com`, and package registries. **No model provider and no
weight host.**

A locally hosted real model is also unavailable: every route to open weights
(HuggingFace, its CDN, its mirrors, ModelScope, the Ollama registry) is refused
by the same policy, and PyPI carries no usable instruct-model weights.

## What is NOT the problem

- **Not a missing key.** `GROQ_API_KEY` is present in this environment. The
  refusal happens at CONNECT, before any credential is offered.
- **Not the provider code.** `kgq/provider.py` is proven against a real HTTP
  server speaking `/chat/completions` — 11 tests in
  `tests/test_provider_transport.py` covering request shape, auth header,
  response parsing, usage accounting, error handling and the response cache.
- **Not the corpus.** See below: the required corpus is present and compiles
  clean.

## Why no substitute was used

§5 of the J-2 brief: *"If no real model is reachable, report J-2 BLOCKED. Do
not substitute a fake model and call J-2 complete."*

The scripted stand-in used for the Demo 0.2 screenshots is not a language model
and cannot produce a semantic-entailment measurement. Running it here would
measure the validator against text I wrote, which is worth nothing and would
corrupt the record. It was not used.

## Pre-flight that DID complete (§1, §2)

The system under test is frozen and the corpus requirement is met.
`eval/j2/J2_SYSTEM_UNDER_TEST.json` records:

| Field | Value |
|---|---|
| compiler commit | `7bc625319922393bd62313c2a47b845d1aa94616` |
| schema version | `1` |
| corpus | `https://github.com/pallets/werkzeug` |
| corpus commit | `6048fa48753c7b61e35cc34537667809dee8fa35` — **matches the commit J-2 requires** |
| corpus content sha256 | `19f4028a0a9f15b4e77d41ad489ee3368b72b309c9ac2836b1d34941fc32d35f` |
| database | fresh, built for J-2, contains no previous experiment |
| artifacts / symbols / claims / evidence | 225 / 6 102 / 16 244 / 16 066 |
| invariant violations | `[]` |
| compile time | 6.3 s |

So there is exactly **one** blocker, not two.

## What unblocks it

Network access for this environment is set in the cloud environment menu in the
session title bar → **Edit** → **Network access**: either a broader access
level, or `api.groq.com` added to the allowed domains. Access levels are
described at https://code.claude.com/docs/en/claude-code-on-the-web.

With that host allowed, the primary run needs only:

```bash
export KGQ_BASE_URL=https://api.groq.com/openai/v1
export KGQ_MODEL=<model id>          # e.g. a Llama or Qwen instruct model
export KGQ_API_KEY=$GROQ_API_KEY
```

No code change. The provider interface already in use is the one J-2 specifies.

## What was deliberately NOT done

Per §5 and §22 ("STOP and report the block"), the following were not started:

- the independently authored J-2 query set (§3),
- the independent gold labels (§4),
- any evaluation run, adjudication, metric or report (§6–§21).

The query set and gold are model-independent and could be built while the
network question is resolved — but §3 requires an author who has not seen the
answer implementation, validator, retrieval or the J-1 gold, and that
independence is worth arranging deliberately rather than assuming. It is
offered, not assumed.
