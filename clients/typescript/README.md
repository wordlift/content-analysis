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

## Errors

`unresolved` is a result, not an error. Two exceptions exist:

- `InvalidRequestError` (HTTP 422): a span that does not match the text, an
  identity that is not a Wikidata id, an unsupported `dataset_uri`.
- `ResolverUnavailableError`: network failure, timeout or a non-2xx status.
  Catch it and treat the mention as unresolved; never fall back to a guess.

## Options

```ts
new WordLiftResolver({
  apiKey,                       // required; sent as `Authorization: Key <apiKey>`
  baseUrl,                      // default: the production endpoint
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
