# Changelog

## 0.1.4 — TypeScript client published 2026-10-13 (`@wordlift/resolve@0.1.4`); Python package installs from git

- **The cost header is `X-Wordlift-Cost`** (wordlift/content-analysis-engine#57): both clients read
  a request's cost from `X-Wordlift-Cost`, the header the WordLift gateway books on every route,
  instead of `X-Wordlift-Consumption`, which the gateway never read (every call was booked as
  one credit) and the engine no longer sends. `credits` / `diagnostics["credits"]` stay
  optional: with an engine that still sends only the old name they are absent. Contract and
  OpenAPI document updated.

## 0.1.3 — TypeScript client published 2026-10-11 (`@wordlift/resolve@0.1.3`); Python package installs from git

- README: the repository leads with resolve(), says how to get a key (free, 1,000 credits a month),
  what a call costs, where the open tester and the live demos are, and has a section for agents.

- **The public address is the account route**: `POST https://api.wordlift.io/accounts/{accountId}/resolve`,
  metered by the WordLift gateway. Both clients default to `https://api.wordlift.io`
  and read `accountId` from the key (`GET /accounts/me`) on first use; pass
  `account_id` / `accountId` to skip the lookup, or `endpoint` to call one URL
  as given. `ENGINE_ENDPOINT` is deprecated: that address serves only the
  gateway since 2026-10-11 (direct calls are 404). The CLI
  takes `--account-id` (or `$WL_ACCOUNT_ID`) and `--endpoint`. A key used with
  another account's id is `403` (`AuthorizationError`). Breaking for callers
  that passed `base_url`/`baseUrl` pointing at the engine host: pass it as
  `endpoint` instead.

## 0.1.2 — TypeScript client published 2026-10-11 (`@wordlift/resolve@0.1.2`); Python package installs from git

- One request per document (issue #4): a `BatchResolver` protocol
  (`resolve_many`) with `as_batch()` adapting any single-mention `Resolver`;
  `run()` sends every span and the inline vocabulary once instead of one
  request per mention. `WordLiftResolver` bounds a request to
  `MAX_MENTIONS_PER_REQUEST` (200) mentions and `MAX_TEXT_CHARS` (100,000)
  characters, cutting long texts into chunks that never split a mention, and
  checks every span before the first request. Order and document offsets are
  kept. Errors keep their metadata: 429 becomes `rate_limited` with
  `retry_after` and the problem `code` when there is one; 401/403 raise
  `AuthorizationError`; a 5xx keeps its status; each request's cost is
  reported as `credits`. No automatic retries. `context_radius` now shapes
  retrieval only: the engine reads its own 400-character window per mention.
  `evaluate(..., keep_rows=False)` for large datasets.
- TypeScript: `resolveDocument(text, mentions, options)` mirrors the Python
  batching (spans checked first, one request per 200 mentions and per
  100,000 characters, chunks never cut a mention, document offsets and order
  kept, `credits` summed). Problem JSON is read once (`problemOf`, `Problem`)
  and mapped by status (`failure`): `InvalidRequestError` carries `code`,
  new `AuthorizationError` (401/403) and `RateLimitedError` (429 with
  `retryAfter`; the gateway's plain-text 429 has no `code`),
  `ResolverUnavailableError` keeps `status`, `code` and `retryAfter`;
  `ResolveResponse.credits` from `X-Wordlift-Consumption`. Additive: existing
  callers see no change except that a 429 and a 401/403, which used to be
  `ResolverUnavailableError`, are now their own subclasses of `Error`.

Fixes from the review of 2026-10-07 (issue #2). Identity safety first:

- `run(..., on_invalid_mention="raise" | "unresolved")` (issue #5): a
  mention that does not lie in the text as written raises `ValueError`
  before any request (the default), or comes back as `unresolved` with the
  new reason `invalid_mention` so a batch of gold labels or external NER
  spans carries on.

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
- A 422 is the caller's error, no longer `resolver_unavailable`: the engine
  answered, the request was wrong. `WordLiftResolver` raises
  `InvalidRequestError` (a `ValueError`, with the engine's `detail`), as the
  TypeScript client does; the CLI prints `invalid_request` and exits 1.

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
