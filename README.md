<p align="center">
  <a href="https://wordlift.io/resolve/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/assets/wordlift-logotype-white.png">
      <img src="docs/assets/wordlift-logotype-blue.png" alt="WordLift" width="260">
    </picture>
  </a>
</p>

<h1 align="center">resolve() by WordLift</h1>

<p align="center">
  <b>extract → retrieve → resolve | NIL</b><br>
  The open pipeline, clients and contract for WordLift's entity resolution API: map a mention to an identity in Wikidata, your knowledge graph or your own vocabulary, or get <code>unresolved</code>.<br>
  <a href="https://wordlift.io/resolve/">wordlift.io/resolve</a> · <a href="https://docs.wordlift.io/developer-resources/resolve/">developer guide</a> · <a href="https://docs.wordlift.io/api/resolve/resolve-api/">API reference</a> · <a href="https://wordlift.io/resolve/demos/">live agent demos</a>
</p>

<p align="center">
  <a href="https://github.com/wordlift/content-analysis/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/wordlift/content-analysis/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12-blue.svg">
  <a href="https://www.npmjs.com/package/@wordlift/resolve"><img alt="npm @wordlift/resolve" src="https://img.shields.io/npm/v/%40wordlift%2Fresolve.svg?label=%40wordlift%2Fresolve"></a>
  <a href="https://wordlift.io/resolve/"><img alt="resolve()" src="https://img.shields.io/badge/resolve()-EN%20IT%20FR%20DE%20ES%20PT-3452DB.svg"></a>
</p>

<p align="center">
  <a href="https://wordlift.io/resolve/#access"><img alt="Get a free API key" src="https://img.shields.io/badge/Get%20a%20free%20API%20key-1%2C000%20requests%20a%20month-22A286?style=for-the-badge"></a>
</p>

---

**What it does.** You extract mentions from a document, look them up in your own
knowledge graph or vocabulary, and hand mention, context and candidates to a
`Resolver`. The resolver answers with one of your candidates, or with
`unresolved`. Two resolvers ship here:

| Resolver | What it is | When to use it |
|---|---|---|
| `ArgmaxResolver` | Open reference: best-scored candidate above a floor and margin, else NIL | Running the pipeline with no external service; a baseline to beat |
| `WordLiftResolver` | Client for WordLift's `resolve()` API, `POST /accounts/{accountId}/resolve` | Production: multilingual disambiguation that returns NIL when the evidence is not strong enough |

Open where you build. Proprietary where we resolve.

## Get a key

Every call to the hosted API needs a WordLift key in `Authorization: Key <key>`.

- **New here:** [request a free key](https://wordlift.io/resolve/#access): name, email and use case, and the key arrives by email.
  It comes with 1,000 requests a month. A call counts as one request, plus one per
  1,000 characters of text, so a sentence counts 2 and a 5,000-character article 6.
  Past the allowance the API answers `429` with the reset time; nothing is charged.
- **Already a WordLift customer:** your account key works as it is, with the
  allowance of your plan, and `wordlift://dataset/me` resolves against your graph.
- **Just looking:** the landing page's tester is an open endpoint, no key,
  600 characters, ten calls a minute: `POST https://resolve.wordlift.io/demo/resolve`
  with `{"text": "...", "language": "en"}`. The [live demos](https://wordlift.io/resolve/demos/)
  run three agent workflows on it, with and without resolve().

Put the key in `WL_KEY`; the clients and the CLI read it from there and look up
your account id from it, so nothing else is configured.

## 60-second start

```bash
pip install "resolve-pipeline @ git+https://github.com/wordlift/content-analysis.git"
export WL_KEY=...      # free key: https://wordlift.io/resolve/#access (only WordLiftResolver needs it)
python -m resolve_pipeline "Apple opened a new store in Rome." --language en
```

```
Apple            resolved    Q312         Apple Inc.                score 0.99
Rome             resolved    Q220         Rome                      score 0.98
```

Or in code, with your own candidates:

```python
from resolve_pipeline import Candidate, Mention, WordLiftResolver, run

def retrieve(mention: Mention, context: str) -> list[Candidate]:
    # your knowledge graph or vocabulary defines the world
    if mention.text == "Apple":
        return [Candidate("wd:Q312", "Apple Inc."), Candidate("wd:Q89", "apple, the fruit")]
    return []                      # empty: let the engine retrieve against Wikidata

resolver = WordLiftResolver(api_key="...")
for r in run("Apple opened a new store in Rome.", retrieve=retrieve, resolver=resolver,
             mentions=[Mention("Apple", 0, 5), Mention("Rome", 28, 32)], language="en"):
    print(r.mention.text, r.status, r.entity.id if r.entity else r.reason)
```

Pass `mentions` from your own NER or gold spans, or install the `ner` extra and
let GLiNER find them:

```bash
pip install "resolve-pipeline[ner] @ git+https://github.com/wordlift/content-analysis.git"
```

```python
from resolve_pipeline.extract import load_model
results = run(text, retrieve=retrieve, resolver=resolver, model=load_model(), language="en")
```

## The seam: what crosses it

```python
Mention(text, start, end, label="", score=None)
Candidate(id, label="", description="", types=(), score=None, evidence={})
Resolution(mention, status="resolved"|"unresolved", entity=Entity|None, score=None, reason=None)
```

`unresolved` is a result, not an error. Reasons: `no_suitable_candidate`,
`no_candidates`, `type_conflict`, `low_relevance`; the client adds
`resolver_unavailable` when the engine cannot be reached, `rate_limited` when
it answers 429 (with `retry_after` in the diagnostics), `protocol_error` when
it answers with something the client cannot act on, and `invalid_mention` for
a caller span that is not in the text, so none of these ever becomes a guessed
identity. Identifiers are echoed in the form you sent: `Q312`, `wd:Q312` or a
Wikidata entity URI. Every error the API sends is an RFC 9457 Problem JSON
document with a stable `code` whose `type` links to its page; the list is at
[docs.wordlift.io/developer-resources/resolve-errors](https://docs.wordlift.io/developer-resources/resolve-errors/).

**One request per document.** `run()` retrieves candidates per mention with
`context_radius` characters either side of it, then hands the whole document to
the resolver once through the batch protocol (`BatchResolver.resolve_many`,
`as_batch`). `WordLiftResolver` sends every span and the inline vocabulary in a
single request per 200 mentions (`MAX_MENTIONS_PER_REQUEST`) and per 100,000
characters (`MAX_TEXT_CHARS`, the engine's text limit; chunks never cut a
mention), instead of one request and one vocabulary upload per mention. The
engine decides each mention on the 400 characters either side of it, the same
window the client sent before, so decisions do not change; `context_radius`
now shapes only what your retriever sees. Results keep the order of your
mentions and their document offsets; every span is checked before the first
request. A custom `Resolver` that implements only `resolve()` is adapted and
called mention by mention with its window. The client never retries on its
own: a timed-out request may have been served and metered, so the retry policy
is yours; `diagnostics["credits"]` reports what each call counted against the allowance
(`X-Wordlift-Cost`). A refused key raises `AuthorizationError`, a
rejected request `InvalidRequestError`: caller errors stop the batch. A 429 is
`rate_limited` with `retry_after`; the engine's own 429 also carries its
problem `code`, the gateway's allowance block is plain text and does not.

## The endpoint

```
POST https://api.wordlift.io/accounts/{accountId}/resolve
Authorization: Key <your WordLift key>
```

`accountId` is the `accountId` field of `GET https://api.wordlift.io/accounts/me`
with the same key; the clients read it from the key on first use, so they take
only the key. There is no other public address: `resolve.wordlift.io` is the
gateway's upstream and answers 404 to direct calls.

Six languages: English, Italian, French, German, Spanish, Portuguese; Japanese and Chinese answer too, without a gold set or gate yet
(`language` is detected when omitted). Full request and response contract:
[`docs/resolve-contract.md`](docs/resolve-contract.md).

```bash
WL_ACCOUNT_ID=$(curl -s https://api.wordlift.io/accounts/me -H "Authorization: Key $WL_KEY" | jq .accountId)
curl -s -X POST "https://api.wordlift.io/accounts/$WL_ACCOUNT_ID/resolve" \
  -H "Authorization: Key $WL_KEY" -H "Content-Type: application/json" \
  -d '{"text": "Apple opened a new store in Rome.", "language": "en",
       "mentions": [{"text": "Apple", "start": 0, "end": 5,
                     "candidates": [{"id": "wd:Q312"}, {"id": "wd:Q89"}]}],
       "include": ["candidates"]}'
```

```json
{"mentions": [{"text": "Apple", "start": 0, "end": 5, "status": "resolved",
               "entity": {"id": "wd:Q312", "label": "Apple Inc.", "types": ["Organization"],
                          "same_as": ["https://www.wikidata.org/entity/Q312"]},
               "score": 0.99,
               "signals": {"path": "reranked", "retrieval_prior": 0.87, "match_probability": 0.99, "rescued": false},
               "candidates": [{"id": "wd:Q89"}, {"id": "wd:Q312"}]}],
 "dataset_uri": "wikidata://public", "language": "en", "engine": "content-analysis-v3"}
```

Omit `mentions` and the engine detects them. Omit a mention's `candidates`
and the engine retrieves against `dataset_uri`. `include: ["candidates",
"evidence"]` returns the pool it considered and why it abstained.

`score` is the relevance of the returned entity in [0, 1], not a calibrated
probability; `signals` says how the decision was reached (`path` is `fast`,
`reranked` or `vocabulary`, plus the dictionary prior, the model's match
probability and whether the dictionary rescue applied). Compare scores across
mentions only within the same path. Warm requests answer in about a second
(engine p50 779 ms local-first, 719 ms public-only, measured 2026-09-27); a
graph's first call in a container pays its fetch.

## Your world, not just Wikidata

`dataset_uri` picks where identities come from. Three worlds today:

| `dataset_uri` | Identities | How |
|---|---|---|
| `wikidata://public` (default) | Wikidata QIDs | the engine's knowledge base |
| `wordlift://dataset/me` | your WordLift knowledge graph's IRIs | the engine reads your graph with the same key, named entities only, cached a few minutes |
| `inline` | whatever ids you send | `dataset: {entities: [{id, name, aliases, description, types, same_as}]}`, up to 5,000 entities |
| `wordlift://dataset/me,wikidata://public` | your IRIs where your graph knows the entity, QIDs elsewhere | worlds are tried in order per mention; each answer says which world it came from |

```python
resolver = WordLiftResolver(api_key="...", dataset=[
    {"id": "https://acme.example/kg/acme", "name": "Acme", "aliases": ["ACME Corp"], "types": ["Organization"]},
    {"id": "https://acme.example/kg/rome-office", "name": "Rome", "types": ["Place"]},
])
```

The answer is always one of your ids or `unresolved`: a mention that matches
none of your names or aliases is `no_candidates`, and a match whose context does
not fit is `low_relevance`. Same reranker, same gate, no dictionary shortcut.

The walkthrough, with curl, Python, TypeScript and CLI for each mode, the
JSON shape of a vocabulary, the sameAs bridge and the things to know before
relying on it: [`docs/your-own-data.md`](docs/your-own-data.md). Runnable:
[`examples/resolve_with_your_data.py`](examples/resolve_with_your_data.py).

## MCP adapter (experimental)

The open-source [`clients/mcp`](clients/mcp) adapter exposes `resolve_text` as an MCP tool, forwarding requests to the existing hosted `resolve()` API. It preserves both resolved identities and explicit `unresolved` (NIL) decisions, including optional candidate and evidence diagnostics. The engine remains proprietary.

To run it locally with an MCP client over stdio:

```bash
python -m pip install -r clients/mcp/requirements.txt
export WL_KEY="your-wordlift-key"
python clients/mcp/server.py
```

This version is **local/stdio only**. It does not provide an OAuth-enabled remote endpoint for ChatGPT. Remote access will require OAuth, tenant-safe account-to-key mapping, and a separate deployment decision. See [`clients/mcp/README.md`](clients/mcp/README.md) for configuration and tests.

## For agents

- The one call, the limits and the open tester, in plain text: [resolve.wordlift.io/llms.txt](https://resolve.wordlift.io/llms.txt).
- A local MCP server exposing `resolve_text`: [`clients/mcp`](clients/mcp).
- The contract in one JSON file: [`docs/openapi.resolve.json`](docs/openapi.resolve.json).
- `unresolved` is an answer. Never replace it with a guess, and never treat a transport error as `unresolved`.

## Clients

| Language | Where | Install |
|---|---|---|
| Python | this package, `WordLiftResolver` | `pip install "resolve-pipeline @ git+https://github.com/wordlift/content-analysis.git"` |
| TypeScript / JavaScript | [`clients/typescript`](clients/typescript), zero dependencies, Node 18+ and browsers | `npm install @wordlift/resolve` |
| MCP (experimental, stdio) | [`clients/mcp`](clients/mcp), thin adapter to the hosted API | `pip install -r clients/mcp/requirements.txt` |
| Anything else | [`docs/openapi.resolve.json`](docs/openapi.resolve.json), curated from the live service's schema ([why it differs](docs/resolve-contract.md#api-guidelines)) | `openapi-generator generate -i docs/openapi.resolve.json -g <lang>` |

Every client keeps the same rule: `unresolved` is a result, a transport
failure is an error, and neither ever turns into a guessed identity.

## Measuring: every error has an owner

Candidate recall is not resolution accuracy. If the gold entity never reaches
the resolver, that is a retrieval problem; if it is present and the wrong
identity comes back, that is a resolution problem.

```
gold in candidates?
├── no  → retrieval_miss
└── yes → resolver returned gold?
          ├── yes → correct
          └── no  → abstained (NIL)  |  resolution_error (wrong identity)
```

```bash
python -m resolve_pipeline.evaluate --cases datasets/en_gold.json --predictions preds.jsonl
```

`preds.jsonl` has one row per labeled mention: `doc`, `mention`, `candidates`
(ids offered to the resolver) and `prediction` (id or `null`). Six development
gold sets, one Wikidata QID per mention, are in [`datasets/`](datasets/).

## Status of the NIL-safe decision layer

The endpoint runs on WordLift's production linker today. A stricter,
NIL-aware decision layer is released one language at a time, only after a
frozen held-out gate whose criteria are fixed before the run: accepted
precision ≥ 99%, resolution coverage ≥ 90%, false resolution under forced NIL
≤ 2%. Results so far, misses included:

| Language | Accepted precision | Coverage | Forced-NIL false resolution | Verdict |
|---|---:|---:|---:|---|
| DE, frozen rule replayed on three held-out sets | 129/130 · 99.2% | 129/145 · 89.0% | 3/145 · 2.07% | hold, by one case and two links |
| IT | 37/37 | 37/37 | 3/37 · 8.1% | hold |
| EN | 41/41 | 41/57 · 72% | 1/57 · 1.8% | hold |

Italian and English are single gate runs of 2026-09-23. German was gated three
times that day, once per fresh held-out set (100% / 90% / 5.0%, 98% / 88% /
3.5%, 100% / 94% / 2.1%), with the rule revised on development data between
gates; the row above is that final rule replayed on all three sets on
2026-09-27. The held-out sets are not in this repository.

The recurring failure is a confident link to a closely related identity (a
company for the city it is based in, an agency for its building). When a
language passes, the endpoint switches to the new layer with no contract
change. Progress: https://wordlift.io/resolve/.

## Repository layout

```
resolve_pipeline/
  types.py       Mention, Candidate, Entity, Resolution, reason vocabulary
  resolver.py    Resolver protocol, ArgmaxResolver, WordLiftResolver
  pipeline.py    run(): extract → retrieve → resolve over one document
  extract.py     GLiNER multilingual NER (optional extra)
  html.py        HTML → text with a character offset map (standard library)
  evaluate.py    error attribution CLI
  __main__.py    `python -m resolve_pipeline "text"`
clients/typescript/  @wordlift/resolve, zero dependencies (npm)
clients/mcp/     experimental local MCP adapter (stdio)
datasets/        development gold sets (en, it, fr, de, es, pt)
docs/            resolve() contract, your-own-data guide, OpenAPI, logos
examples/        runnable scripts
tests/           no network, no GPU, no key needed
```

## Developing

```bash
git clone https://github.com/wordlift/content-analysis.git && cd content-analysis
make setup      # venv + editable install with dev extras
make test       # pytest
make coverage   # pytest with line and branch coverage (CI enforces the floor)
make lint       # compile, ruff, mypy
make example    # examples/resolve_with_wordlift.py (needs WL_KEY)
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Wrong resolutions are bugs; report
them with the issue template.

## License

Apache License 2.0, see [LICENSE](LICENSE). WordLift's resolution engine
behind `resolve()` is a separate, proprietary service and is not covered by
this license.
