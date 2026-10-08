<p align="center">
  <a href="https://wordlift.io/resolve/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/assets/wordlift-logotype-white.png">
      <img src="docs/assets/wordlift-logotype-blue.png" alt="WordLift" width="260">
    </picture>
  </a>
</p>

<h1 align="center">Content Analysis pipeline</h1>

<p align="center">
  <b>extract → retrieve → resolve | NIL</b><br>
  An open pipeline for turning text into entity identities, with a pluggable decision step.
</p>

<p align="center">
  <a href="https://github.com/wordlift/content-analysis/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/wordlift/content-analysis/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12-blue.svg">
  <a href="https://www.npmjs.com/package/@wordlift/resolve"><img alt="npm @wordlift/resolve" src="https://img.shields.io/npm/v/%40wordlift%2Fresolve.svg?label=%40wordlift%2Fresolve"></a>
  <a href="https://wordlift.io/resolve/"><img alt="resolve()" src="https://img.shields.io/badge/resolve()-EN%20IT%20FR%20DE%20ES%20PT-3452DB.svg"></a>
</p>

---

**What it does.** You extract mentions from a document, look them up in your own
knowledge graph or vocabulary, and hand mention, context and candidates to a
`Resolver`. The resolver answers with one of your candidates, or with
`unresolved`. Two resolvers ship here:

| Resolver | What it is | When to use it |
|---|---|---|
| `ArgmaxResolver` | Open reference: best-scored candidate above a floor and margin, else NIL | Running the pipeline with no external service; a baseline to beat |
| `WordLiftResolver` | Client for WordLift's `resolve()` engine, `POST /v1/resolve` | Production: multilingual disambiguation that returns NIL when the evidence is not strong enough |

Open where you build. Proprietary where we resolve.

## 60-second start

```bash
pip install "resolve-pipeline @ git+https://github.com/wordlift/content-analysis.git"
export WL_KEY=...      # WordLift API key, only for WordLiftResolver
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
`resolver_unavailable` when the engine cannot be reached, so a transport
failure never becomes a guessed identity. Identifiers are echoed in the form
you sent: `Q312`, `wd:Q312` or a Wikidata entity URI.

## The engine endpoint

```
POST https://resolve.wordlift.io/v1/resolve
Authorization: Key <your WordLift key>
```

Six languages: English, Italian, French, German, Spanish, Portuguese; Japanese and Chinese answer too, without a gold set or gate yet
(`language` is detected when omitted). Full request and response contract:
[`docs/resolve-contract.md`](docs/resolve-contract.md).

```bash
curl -s -X POST https://resolve.wordlift.io/v1/resolve \
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

## Clients

| Language | Where | Install |
|---|---|---|
| Python | this package, `WordLiftResolver` | `pip install "resolve-pipeline @ git+https://github.com/wordlift/content-analysis.git"` |
| TypeScript / JavaScript | [`clients/typescript`](clients/typescript), zero dependencies, Node 18+ and browsers | `npm install @wordlift/resolve` |
| Anything else | [`docs/openapi.resolve.json`](docs/openapi.resolve.json), generated from the live service | `openapi-generator generate -i docs/openapi.resolve.json -g <lang>` |

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
make example    # examples/resolve_with_wordlift.py (needs WL_KEY)
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Wrong resolutions are bugs; report
them with the issue template.

## License

Apache License 2.0, see [LICENSE](LICENSE). WordLift's resolution engine
behind `resolve()` is a separate, proprietary service and is not covered by
this license.
