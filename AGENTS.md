# AGENTS.md

Instructions for coding agents working in this repository. Humans: see README.md.

## What this repository is

The open half of WordLift's entity resolution. It holds a pipeline
(`extract → retrieve → resolve | NIL`), a `Resolver` interface with an open
reference implementation, and clients for WordLift's hosted `resolve()`
engine. The engine itself (models, indexes, decision thresholds) is not here
and is not configurable from here.

## The contract you must not break

- `POST https://resolve.wordlift.io/v1/resolve`, header `Authorization: Key <key>`.
  Full schema: `docs/resolve-contract.md`, machine-readable `docs/openapi.resolve.json`.
  The engine serves its own schema at `https://resolve.wordlift.io/openapi.json`;
  the repo copy is curated and currently ahead of it (see "API guidelines" in
  the contract), so never overwrite it by regenerating from the live service.
- Per mention the answer is `resolved` (with `entity`) or `unresolved` (with a
  `reason`). **Unresolved is a result.** Clients map transport failures to
  `resolver_unavailable` and never fall back to a guessed identity.
- Identifiers are echoed in the caller's form (`Q312`, `wd:Q312`, Wikidata URI).
- The answer for a mention is the one response row with its span, rebased to
  the text that was sent (`Context` carries the window's offset). No row, two
  rows, a resolved row without an identity, or a body of another shape is a
  protocol error (`protocol_error` / `ResolverProtocolError`): never another
  row, never a guessed identity, and distinct from `resolver_unavailable`.
- Empty candidate list means "let the engine retrieve" unless the caller set
  `engine_retrieval=False`.
- User datasets (`wordlift://dataset/me`, inline `dataset`, ordered worlds such
  as `wordlift://dataset/me,wikidata://public`) are documented in
  `docs/your-own-data.md`; with a user dataset, per-mention `candidates` are
  not sent (the engine rejects them with 422).

## Layout

```
resolve_pipeline/          Python package (types, resolver, pipeline, extract, html, evaluate, __main__)
clients/typescript/        @wordlift/resolve, zero dependencies, tests under test/
docs/                      contract, your-own-data guide, OpenAPI document, logos
datasets/                  development gold sets: one Wikidata QID per mention
examples/                  runnable scripts (need WL_KEY)
tests/                     Python tests: no network, no GPU, no key
```

## How to work

- Setup: `make setup` (venv + `pip install -e ".[dev]"`); tests: `make test`;
  coverage: `make coverage`; lint: `make lint`. TypeScript:
  `cd clients/typescript && npm ci && npm test` (`npm run coverage` for coverage).
- Coverage target: **90% or more** on every metric CI measures: Python line
  and branch coverage combined, TypeScript lines, functions and branches. CI
  enforces it (Python `fail_under` in `pyproject.toml`, TypeScript thresholds
  in the `coverage` script). A change that adds code adds the tests that keep
  it at or above 90%. Raise the floors when coverage rises; never lower them
  to make a change pass.
- Tests must stay offline. Engine calls in tests go through a mocked
  transport (`httpx.MockTransport` in Python, an injected `fetch` in TS).
- Verify anything that touches a client against production once, with a real
  key from the environment (`WL_KEY`), and paste the output in the PR.
- Gold data: add or change a mention only with a QID verified against
  Wikidata at authoring time, and say how in the PR.
- Do not add engine internals, thresholds, holdout fixtures, keys or any
  upstream hostname. The only public host is `resolve.wordlift.io`.
- Releases of the TypeScript client are tag-driven (`ts-v<version>`, see
  `.github/workflows/release-npm.yml`); bump the version with
  `npm version <x> --no-git-tag-version` in `clients/typescript` (it updates
  `package.json` and `package-lock.json` together) and update `CHANGELOG.md`
  in the same commit.

## Where things are decided

Gate results, release decisions and the reasoning behind the NIL rules are
published at https://wordlift.io/resolve/ and summarized in README.md. If a
change here depends on engine behaviour, say so explicitly rather than
assuming it.

- Resolution goes to the engine once per document (`Resolver.resolve_many`;
  `run()` falls back to `resolve()` for a resolver without it). Never reintroduce
  a request per mention: with an inline vocabulary that re-uploads the whole
  vocabulary for every mention (issue #4). The client does not retry: a
  timed-out request may have been served and metered.

