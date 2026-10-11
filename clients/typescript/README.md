# @wordlift/resolve

TypeScript client for WordLift `resolve()`: give it a mention, its context and
your candidate identities; get back the supported identity, or `unresolved`.
Zero dependencies, uses the global `fetch` (Node 18+, browsers, edge runtimes).

```bash
npm install @wordlift/resolve
```

```ts
import { WordLiftResolver } from "@wordlift/resolve";

const wl = new WordLiftResolver({ apiKey: process.env.WL_KEY! });

const res = await wl.resolve({
  text: "Apple opened a new store in Rome.",
  language: "en",
  mentions: [
    { text: "Apple", start: 0, end: 5, candidates: [{ id: "wd:Q312" }, { id: "wd:Q89" }] },
    { text: "Rome", start: 28, end: 32 },            // no candidates: the engine retrieves
  ],
  include: ["candidates"],
});

for (const m of res.mentions) {
  console.log(m.text, m.status, m.entity?.id ?? m.reason);
}
// Apple resolved wd:Q312
// Rome  resolved Q220
```

One mention, one answer:

```ts
const m = await wl.resolveMention("Fresh bread at Apple Street Bakery.",
  { text: "Apple Street Bakery", start: 15, end: 34, candidates: [{ id: "wd:Q312" }] });
m.status;   // "unresolved"
m.reason;   // "low_relevance"
```

## One request per document

`resolveDocument()` takes the whole text and every mention, checks each span
before anything is sent, and resolves them in as few requests as possible: one
per 200 mentions and per 100,000 characters (the engine's text limit; long
documents are cut at whitespace, never through a mention). With an inline
`dataset` the vocabulary travels once. Rows come back in the order of your
mentions with document offsets, and `credits` says what the document cost.

```ts
const doc = await wl.resolveDocument(article, spans, { language: "en" });
doc.mentions[3].entity?.id;   // same order as `spans`, offsets in `article`
doc.requests;                 // 1 for most articles
doc.credits;                  // smart credits charged, summed over the requests
```

The engine decides each mention on the 400 characters either side of it, so one
request per document gives the same decisions as one request per mention.

## Errors

`unresolved` is a result, not an error. Every error body under `/v1/` is RFC 9457
Problem JSON; its stable `code` is on the error, and `type` leads to the code's
page under https://docs.wordlift.io/problems/.

- `InvalidMentionError`: a span you passed to `resolveDocument()` does not hold
  its text. Thrown before any request.
- `InvalidRequestError` (HTTP 422): the engine refused the request; `code` is
  `invalid_span`, `invalid_identity`, `dataset_not_supported`, ... and `detail`
  the sentence or the validation errors.
- `AuthorizationError` (401, 403): the key was refused. Nothing in the request
  can be resolved.
- `RateLimitedError` (429): wait `retryAfter` seconds and send it again. The
  engine's own 429 (`code: "too_many_requests"`, its queue is full) and the
  gateway's allowance block (plain text, no `code`, `X-RateLimit-*` headers)
  both arrive here. Nothing was resolved or charged.
- `ResolverUnavailableError`: network failure, timeout or another non-2xx status
  (`status`, `code` and `retryAfter` when given, for example `dataset_warming`).
  Catch it and treat the mention as unresolved; never fall back to a guess.
- `ResolverProtocolError`: the engine answered with something the client cannot
  act on (no row for a span, two rows, a resolved row without an identity).

The client never retries on its own: a request that timed out may still have
been served and metered, so the retry policy is yours.

## Options

```ts
new WordLiftResolver({
  apiKey,                       // required; sent as `Authorization: Key <apiKey>`
  baseUrl,                      // default: https://api.wordlift.io
  accountId,                    // your account id; read from the key (GET /accounts/me) when omitted
  endpoint,                     // one URL as given (a private engine deployment), skips the lookup
  datasetUri: "wikidata://public",
  timeoutMs: 30000,
  fetch,                        // custom fetch for tests or special runtimes
});
```

The full request and response contract is in
[`docs/resolve-contract.md`](../../docs/resolve-contract.md); the OpenAPI
document for code generation in other languages is
[`docs/openapi.resolve.json`](../../docs/openapi.resolve.json).

## Developing

```bash
npm install
npm test          # builds with tsc, runs node --test against a mocked fetch
```

## Releasing

Releases are published by `.github/workflows/release-npm.yml` from a tag:

```bash
npm version patch --no-git-tag-version     # bumps package.json, e.g. 0.1.1
git commit -am "typescript client 0.1.1" && git tag ts-v0.1.1 && git push && git push --tags
```

The workflow checks that the tag matches `package.json`, runs the tests and
publishes with provenance. Authentication is npm trusted publishing (enable it
once on npmjs.com: package Settings → Trusted Publisher → GitHub Actions,
repository `wordlift/content-analysis`, workflow `release-npm.yml`); until
then a repository secret `NPM_TOKEN` is used if present.

Apache-2.0.
