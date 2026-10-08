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

import { ResolverProtocolError, validateResponse } from "../dist/index.js";

const ok = (mentions, extra = {}) => ({ mentions, dataset_uri: "wikidata://public", language: "en", processing_time_ms: 1, ...extra });

test("another row is never the answer for the requested mention", async () => {
  const wl = new WordLiftResolver({ apiKey: "k", fetch: mockFetch(200, ok([
    { text: "Rome", start: 28, end: 32, status: "resolved", entity: { id: "Q220", label: "Rome" } }])) });
  await assert.rejects(wl.resolveMention(TEXT, { text: "Apple", start: 0, end: 5 }), ResolverProtocolError);
  const none = new WordLiftResolver({ apiKey: "k", fetch: mockFetch(200, ok([])) });
  await assert.rejects(none.resolveMention(TEXT, { text: "Apple", start: 0, end: 5 }), ResolverProtocolError);
  const row = { text: "Apple", start: 0, end: 5, status: "resolved", entity: { id: "Q312", label: "Apple Inc." } };
  const twice = new WordLiftResolver({ apiKey: "k", fetch: mockFetch(200, ok([row, { ...row, entity: { id: "Q89", label: "apple" } }])) });
  await assert.rejects(twice.resolveMention(TEXT, { text: "Apple", start: 0, end: 5 }), ResolverProtocolError);
  const exact = new WordLiftResolver({ apiKey: "k", fetch: mockFetch(200, ok([row])) });
  assert.equal((await exact.resolveMention(TEXT, { text: "Apple", start: 0, end: 5 })).entity.id, "Q312");
});

test("malformed successful responses are protocol errors; unknown fields pass", async () => {
  for (const body of [null, [], { mentions: null }, ok([{ start: 0, end: 5, status: "resolved", entity: null }]),
                      ok([{ start: 0, end: 5, status: "resolved", entity: { label: "no id" } }]),
                      ok([{ start: 0, end: 5, status: "maybe" }]), ok([{ status: "unresolved" }])]) {
    assert.throws(() => validateResponse(body), ResolverProtocolError);
  }
  const html = new WordLiftResolver({ apiKey: "k", fetch: async () => new Response("<html>maintenance</html>", { status: 200 }) });
  await assert.rejects(html.resolve({ text: TEXT }), ResolverProtocolError);
  const minimal = validateResponse(ok([{ start: 0, end: 5, status: "resolved", entity: { id: "Q312", label: "Apple Inc." }, future: 1 }], { extra: true }));
  assert.equal(minimal.mentions[0].entity.id, "Q312");
  assert.equal(minimal.mentions[0].entity.types, undefined);     // optional, as the schema says
});

test("the timeout covers a stalled body and body errors are unavailability", async () => {
  const stalled = async (url, init) => new Response(new ReadableStream({
    start(controller) {
      const chunk = new TextEncoder().encode(JSON.stringify(ok([])));
      const t = setTimeout(() => { controller.enqueue(chunk); controller.close(); }, 200);
      init.signal.addEventListener("abort", () => { clearTimeout(t); controller.error(new Error("aborted")); });
    },
  }), { status: 200, headers: { "content-type": "application/json" } });
  const wl = new WordLiftResolver({ apiKey: "k", timeoutMs: 20, fetch: stalled });
  const t0 = Date.now();
  await assert.rejects(wl.resolve({ text: TEXT }), ResolverUnavailableError);
  assert.ok(Date.now() - t0 < 150, "aborted by the timeout, not by the body");
  const broken = async () => new Response(new ReadableStream({ start(c) { c.error(new Error("stream reset")); } }), { status: 200 });
  await assert.rejects(new WordLiftResolver({ apiKey: "k", fetch: broken }).resolve({ text: TEXT }), ResolverUnavailableError);
});
