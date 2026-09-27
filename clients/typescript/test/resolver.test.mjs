import test from "node:test";
import assert from "node:assert/strict";
import { WordLiftResolver, ResolverUnavailableError, InvalidRequestError } from "../dist/index.js";

const TEXT = "Apple opened a new store in Rome.";
const mockFetch = (status, body, seen = {}) => async (url, init) => {
  seen.url = url; seen.init = init; seen.body = JSON.parse(init.body);
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
};

test("sends the contract shape with the key header and returns the resolved mention", async () => {
  const seen = {};
  const wl = new WordLiftResolver({ apiKey: "k", baseUrl: "https://example.test/", fetch: mockFetch(200, {
    mentions: [{ text: "Apple", start: 0, end: 5, status: "resolved",
                 entity: { id: "wd:Q312", label: "Apple Inc.", types: ["Organization"], same_as: [] }, score: 0.97 }],
    dataset_uri: "wikidata://public", language: "en", processing_time_ms: 12, engine: "content-analysis-v3",
  }, seen) });
  const m = await wl.resolveMention(TEXT, { text: "Apple", start: 0, end: 5, candidates: [{ id: "wd:Q312" }, { id: "wd:Q89" }] }, { language: "en" });
  assert.equal(seen.url, "https://example.test/v1/resolve");
  assert.equal(seen.init.headers.Authorization, "Key k");
  assert.equal(seen.body.dataset_uri, "wikidata://public");
  assert.deepEqual(seen.body.mentions[0].candidates, [{ id: "wd:Q312" }, { id: "wd:Q89" }]);
  assert.equal(m.status, "resolved");
  assert.equal(m.entity.id, "wd:Q312");
});

test("unresolved is a result, not an error", async () => {
  const wl = new WordLiftResolver({ apiKey: "k", fetch: mockFetch(200, {
    mentions: [{ text: "Apple", start: 0, end: 5, status: "unresolved", entity: null, reason: "low_relevance" }],
    dataset_uri: "wikidata://public", language: "en", processing_time_ms: 5, engine: "content-analysis-v3",
  }) });
  const m = await wl.resolveMention(TEXT, { text: "Apple", start: 0, end: 5 });
  assert.equal(m.status, "unresolved");
  assert.equal(m.reason, "low_relevance");
  assert.equal(m.entity, null);
});

test("422 is an InvalidRequestError, other failures are ResolverUnavailableError", async () => {
  const bad = new WordLiftResolver({ apiKey: "k", fetch: mockFetch(422, { detail: { error: "invalid_span" } }) });
  await assert.rejects(bad.resolve({ text: TEXT }), InvalidRequestError);
  const down = new WordLiftResolver({ apiKey: "k", fetch: mockFetch(503, "down") });
  await assert.rejects(down.resolve({ text: TEXT }), ResolverUnavailableError);
  const net = new WordLiftResolver({ apiKey: "k", fetch: async () => { throw new Error("ECONNRESET"); } });
  await assert.rejects(net.resolve({ text: TEXT }), ResolverUnavailableError);
});

test("apiKey is required", () => {
  assert.throws(() => new WordLiftResolver({ apiKey: "" }), /apiKey/);
});
