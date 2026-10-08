# Changelog

## 0.1.2 — unreleased

Fixes from the review of 2026-10-07 (issue #2). Identity safety first:

- The context window carries its offset (`Context`, a `str` with an
  `offset`, so retrievers and resolvers written for plain text keep working),
  the request span is rebased to the text sent, and resolutions keep document
  offsets. A mention that does not lie in its context as written raises
  `ValueError` before any request: it is the caller's error, not the
  engine's. Before this a mention beyond the window's start was sent with
  document offsets into a cropped text.
- No fallback row: the answer for a mention is the one row with its span.
  No row, two rows, a resolved row without an identity or a body of another
  shape is `protocol_error` (Python) or `ResolverProtocolError` (TypeScript),
  distinct from `resolver_unavailable` and from a genuine `unresolved`.
- HTML: offsets come from the parser's source positions; entities map to
  their source, attributes, scripts and comments are never matched, inline
  whitespace is one space, the text is not trimmed, an omitted `</head>`
  keeps the body, and an unknown reference such as the `&T` of `AT&T` stays
  as written. Standard library only;
  the `html` extra is no longer needed.
- TypeScript: the timeout covers the body; `types`, `same_as` and `engine`
  optional as the schema says. **Breaking for TypeScript callers** under
  `strictNullChecks`: read `entity.types ?? []` instead of `entity.types`.
- CLI: candidates are refused with any user world; a missing score prints.
- `ArgmaxResolver` ignores nonfinite scores. The evaluator treats `Q312`,
  `wd:Q312` and the Wikidata URIs as one identity.
- OpenAPI: the security scheme reference, `dataset`, per-mention
  `dataset_uri` and `signals`, the 401, 429 and 503 responses, the
  consumption and rate-limit headers, contact and audience metadata; a test
  keeps it consistent.
- Extraction serialises the splitter assignment and the prediction per model
  (a lock per model, so different models still run in parallel);
  `WordLiftResolver` closes the HTTP client it owns (context manager).
- Standards follow-up to the review: every number in the OpenAPI document
  declares its format (offsets `int32`, counters and seconds `int64`, scores
  `double`) and a test keeps it so; the contract records its deviations from
  the Zalando guidelines and why the specification is JSON; the npm lockfile
  is committed and CI and release install with `npm ci`; `make lint` and a
  CI lint job run ruff and mypy (pinned) as well as byte-compiling. The repo
  OpenAPI copy is documented as curated and ahead of the live schema.
- CLI tests (options refused before any request, the request body, output,
  exit codes) with a mocked transport. Coverage runs in CI: Python line and
  branch coverage with a 90% floor (`make coverage`), the TypeScript client
  at 100% lines and functions and 90% branches (`npm run coverage`).
- CLI: a 422 is `invalid_request` with the engine's `detail` and exit code
  1, no longer `resolver_unavailable`: the engine answered, the request was
  wrong.

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
