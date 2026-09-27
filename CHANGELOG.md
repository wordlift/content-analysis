# Changelog

## 0.1.1 — unreleased

- `signals` on every mention (`path`, `retrieval_prior`, `match_probability`,
  `rescued`); typed in the TypeScript client, kept in `Resolution.diagnostics`
  by the Python client. `score` documented as relevance, with the roadmap to
  confidence in the contract.
- `docs/your-own-data.md`: running resolve() on your WordLift graph, an
  inline vocabulary, and local-first with Wikidata as the fallback;
  `examples/resolve_with_your_data.py`.
- CLI: `--dataset-uri` and `--dataset FILE`; the answering world is printed
  when more than one world is queried.
- Clients, README, CLI and OpenAPI document point at the branded host
  `https://resolve.wordlift.io`.

## 0.1.0 — 2026-09-23

First public release.

- `Resolver` protocol with `ArgmaxResolver` (open reference) and
  `WordLiftResolver` (client for `POST /v1/resolve`, six languages,
  bring-your-own candidates, `unresolved` on transport failure).
- `run()`: extract → retrieve → resolve over one document; GLiNER extraction
  and HTML offset mapping as optional extras.
- `python -m resolve_pipeline "text"` command line.
- Error-attribution evaluator (`resolve_pipeline.evaluate`) separating
  retrieval misses from resolution errors and abstentions.
- Six development gold sets (en, it, fr, de, es, pt) and the `resolve()`
  contract draft.
