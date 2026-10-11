import test from "node:test";
import assert from "node:assert/strict";
import {
  WordLiftResolver, AuthorizationError, ResolverProtocolError, ResolverUnavailableError, DEFAULT_BASE_URL, ENGINE_ENDPOINT,
} from "../dist/index.js";

const TEXT = "I bought an Apple laptop in Rome.";
const ok = { mentions: [{ start: 12, end: 17, status: "resolved", entity: { id: "Q312", label: "Apple Inc." } }],
             dataset_uri: "wikidata://public", language: "en", processing_time_ms: 1 };
const api = (calls, { meStatus = 200, meBody = { accountId: 216, datasetId: "x" } } = {}) => async (url, init) => {
  calls.push([init.method, url, init.headers.Authorization]);
  if (url.endsWith("/accounts/me")) return new Response(JSON.stringify(meBody), { status: meStatus, headers: { "content-type": "application/json" } });
  return new Response(JSON.stringify(ok), { status: 200, headers: { "content-type": "application/json" } });
};

test("the account is read from the key once and requests go to its resolve route", async () => {
  assert.equal(DEFAULT_BASE_URL, "https://api.wordlift.io");
  const calls = [];
  const wl = new WordLiftResolver({ apiKey: "k", baseUrl: "https://example.test/", fetch: api(calls) });
  assert.equal(wl.accountId, undefined);
  const [a, b] = await Promise.all([wl.resolve({ text: TEXT }), wl.resolve({ text: TEXT })]);
  assert.equal(a.mentions[0].entity.id, "Q312");
  assert.equal(b.mentions.length, 1);
  assert.deepEqual(calls, [["GET", "https://example.test/accounts/me", "Key k"],
                           ["POST", "https://example.test/accounts/216/resolve", "Key k"],
                           ["POST", "https://example.test/accounts/216/resolve", "Key k"]]);
  assert.equal(wl.accountId, "216");
  assert.equal(await wl.endpoint(), "https://example.test/accounts/216/resolve");
});

test("a given account id or endpoint skips the lookup", async () => {
  const calls = [];
  await new WordLiftResolver({ apiKey: "k", baseUrl: "https://example.test", accountId: 7, fetch: api(calls) }).resolve({ text: TEXT });
  assert.deepEqual(calls.map((c) => c[1]), ["https://example.test/accounts/7/resolve"]);
  calls.length = 0;
  const direct = new WordLiftResolver({ apiKey: "k", endpoint: ENGINE_ENDPOINT, fetch: api(calls) });
  await direct.resolve({ text: TEXT });
  assert.deepEqual(calls.map((c) => c[1]), ["https://resolve.wordlift.io/v1/resolve"]);
  assert.equal(direct.accountId, undefined);
});

test("a refused key at the lookup is an AuthorizationError; a failing API is unavailability; a wrong API is a protocol error", async () => {
  for (const status of [401, 403]) {
    const wl = new WordLiftResolver({ apiKey: "k", fetch: api([], { meStatus: status, meBody: { title: "Unauthorized", code: "unauthorized" } }) });
    await assert.rejects(wl.resolve({ text: TEXT }), (err) => err instanceof AuthorizationError && err.status === status);
  }
  await assert.rejects(new WordLiftResolver({ apiKey: "k", fetch: api([], { meStatus: 503, meBody: {} }) }).resolve({ text: TEXT }),
                       (err) => err instanceof ResolverUnavailableError && err.status === 503);
  await assert.rejects(new WordLiftResolver({ apiKey: "k", fetch: api([], { meBody: { something: "else" } }) }).resolve({ text: TEXT }), ResolverProtocolError);
  await assert.rejects(new WordLiftResolver({ apiKey: "k", fetch: api([], { meBody: [1, 2] }) }).resolve({ text: TEXT }), ResolverProtocolError);
});

test("a failed lookup is retried on the next call, a successful one is kept", async () => {
  let fail = true;
  const calls = [];
  const flaky = async (url, init) => {
    if (fail && url.endsWith("/accounts/me")) { fail = false; throw new Error("ECONNRESET"); }
    return api(calls)(url, init);
  };
  const wl = new WordLiftResolver({ apiKey: "k", baseUrl: "https://example.test", fetch: flaky });
  await assert.rejects(wl.resolve({ text: TEXT }), ResolverUnavailableError);
  assert.equal((await wl.resolve({ text: TEXT })).mentions.length, 1);
  assert.deepEqual(calls.map((c) => c[0]), ["GET", "POST"]);
  await wl.resolve({ text: TEXT });
  assert.deepEqual(calls.map((c) => c[0]), ["GET", "POST", "POST"]);
});
