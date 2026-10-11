import test from "node:test";
import assert from "node:assert/strict";
import {
  WordLiftResolver, AuthorizationError, InvalidMentionError, InvalidRequestError, RateLimitedError,
  ResolverProtocolError, ResolverUnavailableError, chunkDocument, failure, problemOf,
} from "../dist/index.js";

const TEXT = "Apple opened a store in Rome. Tim Cook flew to Paris. Apple again.";
const SPANS = [
  { text: "Apple", start: 0, end: 5 }, { text: "Rome", start: 24, end: 28 }, { text: "Tim Cook", start: 30, end: 38 },
  { text: "Paris", start: 47, end: 52 }, { text: "Apple", start: 54, end: 59 },
];

/** An engine that answers every span it is sent with Q<start>, and records the requests. */
const engine = (calls, { headers = {}, rows } = {}) => async (url, init) => {
  const body = JSON.parse(init.body);
  calls.push(body);
  const mentions = rows ?? body.mentions.map((m) => ({ text: m.text, start: m.start, end: m.end, status: "resolved",
                                                        entity: { id: `Q${m.start}`, label: m.text }, score: 0.9 }));
  return new Response(JSON.stringify({ mentions, dataset_uri: "wikidata://public", language: "en", processing_time_ms: 1 }),
                      { status: 200, headers: { "content-type": "application/json", ...headers } });
};
const refusing = (status, body, headers = {}) => async () =>
  new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers });

test("one request carries every span and the vocabulary once; order, offsets and credits kept", async () => {
  const calls = [];
  const dataset = { entities: [{ id: "v:apple", name: "Apple", description: "x".repeat(1000) }] };
  const wl = new WordLiftResolver({ apiKey: "k", accountId: "1", fetch: engine(calls, { headers: { "X-Wordlift-Consumption": "2" } }) });
  const res = await wl.resolveDocument(TEXT, SPANS, { language: "en", dataset_uri: "inline", dataset });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].mentions.length, 5);
  assert.deepEqual(calls[0].dataset, dataset);
  assert.deepEqual(res.mentions.map((m) => m.entity.id), ["Q0", "Q24", "Q30", "Q47", "Q54"]);
  assert.deepEqual(res.mentions.map((m) => [m.start, m.end]), SPANS.map((m) => [m.start, m.end]));
  assert.deepEqual([res.requests, res.credits], [1, 2]);
});

test("batches are bounded and credits are summed, or absent when a request did not report them", async () => {
  const calls = [];
  const saved = WordLiftResolver.MAX_MENTIONS_PER_REQUEST;
  WordLiftResolver.MAX_MENTIONS_PER_REQUEST = 2;
  try {
    let n = 0;
    const counting = async (url, init) => engine(calls, { headers: ++n < 3 ? { "X-Wordlift-Consumption": "1" } : {} })(url, init);
    const res = await new WordLiftResolver({ apiKey: "k", accountId: "1", fetch: counting }).resolveDocument(TEXT, SPANS);
    assert.equal(calls.length, 3);
    assert.deepEqual(res.mentions.map((m) => m.entity.id), ["Q0", "Q24", "Q30", "Q47", "Q54"]);
    assert.equal(res.credits, undefined);                      // the third request had no header
    const all = await new WordLiftResolver({ apiKey: "k", accountId: "1", fetch: engine([], { headers: { "X-Wordlift-Consumption": "1" } }) }).resolveDocument(TEXT, SPANS);
    assert.deepEqual([all.requests, all.credits], [3, 3]);
  } finally {
    WordLiftResolver.MAX_MENTIONS_PER_REQUEST = saved;
  }
});

test("every span is checked before the first request", async () => {
  const calls = [];
  const wl = new WordLiftResolver({ apiKey: "k", accountId: "1", fetch: engine(calls) });
  for (const bad of [{ text: "Rome", start: 0, end: 4 }, { text: "Apple", start: -1, end: 4 }, { text: "x", start: 60, end: 70 }, { text: "", start: 5, end: 5 }]) {
    await assert.rejects(wl.resolveDocument(TEXT, [...SPANS, bad]), InvalidMentionError);
  }
  assert.equal(calls.length, 0);
  const res = await wl.resolveDocument(TEXT, []);
  assert.deepEqual(res, { mentions: [], requests: 0, credits: 0 });
});

test("long documents are cut into chunks that never split a mention; answers come back with document offsets", async () => {
  const text = "word ".repeat(30000).trim();                 // 149,999 characters
  const boundary = 99_995;
  const straddling = { text: text.slice(boundary - 3, boundary + 6), start: boundary - 3, end: boundary + 6 };
  const chunks = chunkDocument(text, [straddling], 100_000);
  assert.deepEqual([chunks[0][0], chunks.at(-1)[1]], [0, text.length]);
  for (const [a, b] of chunks) {
    assert.ok(b - a <= 100_000);
    assert.ok(!(a < straddling.start && straddling.start < b && b < straddling.end));
  }
  assert.deepEqual(chunkDocument("short", [], 100), [[0, 5]]);
  const calls = [];
  const spans = [{ text: "word", start: 0, end: 4 }, straddling, { text: "word", start: text.length - 4, end: text.length }];
  const res = await new WordLiftResolver({ apiKey: "k", accountId: "1", fetch: engine(calls) }).resolveDocument(text, spans);
  assert.equal(calls.length, 2);
  assert.ok(calls.every((c) => c.text.length <= 100_000));
  assert.deepEqual(res.mentions.map((m) => m.start), [0, straddling.start, text.length - 4]);
  assert.equal(res.mentions[1].entity.id, `Q${straddling.start - chunks[1][0]}`);   // answered in the second chunk, rebased
  const noSpace = "x".repeat(250);
  assert.deepEqual(chunkDocument(noSpace, [], 100), [[0, 100], [100, 200], [200, 250]]);
  const mentionAtCut = { text: "bb", start: 99, end: 101 };
  const withMention = "a".repeat(99) + "bb" + "c".repeat(149);
  assert.equal(chunkDocument(withMention, [mentionAtCut], 100)[0][1], 99);  // no space to step back to: cut before the mention
});

test("a mention longer than a chunk is sent whole rather than cut or dropped", async () => {
  const saved = WordLiftResolver.MAX_TEXT_CHARS;
  WordLiftResolver.MAX_TEXT_CHARS = 20;
  try {
    const text = "a ".repeat(10) + "b".repeat(30) + " c".repeat(10);
    const long = { text: "b".repeat(30), start: 20, end: 50 };
    assert.deepEqual(chunkDocument(text, [long], 20), [[0, 19], [19, 20], [20, 50], [50, 70]]);
    const calls = [];
    const res = await new WordLiftResolver({ apiKey: "k", accountId: "1", fetch: engine(calls) }).resolveDocument(text, [long, { text: "c", start: 69, end: 70 }]);
    assert.equal(calls.length, 2);                              // chunks without a mention send nothing
    assert.deepEqual(calls.map((c) => c.text), [long.text, text.slice(50, 70)]);
    assert.deepEqual(res.mentions.map((m) => [m.start, m.end, m.entity.id]), [[20, 50, "Q0"], [69, 70, "Q19"]]);
  } finally {
    WordLiftResolver.MAX_TEXT_CHARS = saved;
  }
});

test("a span with no row or two rows is a protocol error for the document", async () => {
  const one = [{ text: "Apple", start: 0, end: 5, status: "resolved", entity: { id: "Q312" } }];
  await assert.rejects(new WordLiftResolver({ apiKey: "k", accountId: "1", fetch: engine([], { rows: one }) }).resolveDocument(TEXT, SPANS.slice(0, 2)), ResolverProtocolError);
  await assert.rejects(new WordLiftResolver({ apiKey: "k", accountId: "1", fetch: engine([], { rows: [...one, ...one] }) }).resolveDocument(TEXT, SPANS.slice(0, 1)), ResolverProtocolError);
});

test("the engine's 429 carries its code and Retry-After; the gateway's is plain text without a code", async () => {
  const problem = { type: "https://docs.wordlift.io/problems/too_many_requests", title: "Too many requests", status: 429,
                    code: "too_many_requests", detail: "full" };
  const wl = new WordLiftResolver({ apiKey: "k", accountId: "1", fetch: refusing(429, problem, { "content-type": "application/problem+json", "Retry-After": "5" }) });
  await assert.rejects(wl.resolve({ text: TEXT }), (err) => {
    assert.ok(err instanceof RateLimitedError);
    assert.deepEqual([err.status, err.code, err.retryAfter], [429, "too_many_requests", 5]);
    assert.match(err.message, /retry after 5s/);
    return true;
  });
  const gateway = new WordLiftResolver({ apiKey: "k", accountId: "1", fetch: refusing(429, "Rate limit exceeded for resolve", { "content-type": "text/plain", "X-RateLimit-Remaining": "0" }) });
  await assert.rejects(gateway.resolveDocument(TEXT, SPANS.slice(0, 1)), (err) => {
    assert.ok(err instanceof RateLimitedError);
    assert.deepEqual([err.code, err.retryAfter, err.body], [undefined, undefined, "Rate limit exceeded for resolve"]);
    assert.equal(err.message, "resolve() rate limited");
    return true;
  });
});

test("401 and 403 are AuthorizationError; 422 carries its problem code; a 5xx keeps status, code and Retry-After", async () => {
  for (const status of [401, 403]) {
    const wl = new WordLiftResolver({ apiKey: "k", accountId: "1", fetch: refusing(status, { title: "Unauthorized", status, code: "unauthorized" }) });
    await assert.rejects(wl.resolve({ text: TEXT }), (err) => err instanceof AuthorizationError && err.status === status && err.code === "unauthorized" && err.detail === "Unauthorized");
  }
  const invalid = new WordLiftResolver({ apiKey: "k", accountId: "1", fetch: refusing(422, { title: "Invalid span", status: 422, code: "invalid_span", detail: "mention 0", mention: 0 }) });
  await assert.rejects(invalid.resolve({ text: TEXT }), (err) => err instanceof InvalidRequestError && err.code === "invalid_span" && err.detail === "mention 0" && err.problem.status === 422);
  const warming = new WordLiftResolver({ apiKey: "k", accountId: "1", fetch: refusing(503, { code: "dataset_warming", retry_after_s: 30 }, { "Retry-After": "30" }) });
  await assert.rejects(warming.resolve({ text: TEXT }), (err) => err instanceof ResolverUnavailableError && err.status === 503 && err.code === "dataset_warming" && err.retryAfter === 30);
});

test("problemOf reads a body once, from Problem JSON, another object, text or nothing", () => {
  const p = problemOf(422, { type: "https://docs.wordlift.io/problems/invalid_span", title: "Invalid span", code: "invalid_span", mention: 0 }, new Headers());
  assert.deepEqual([p.code, p.type, p.title, p.detail, p.retryAfter], ["invalid_span", "https://docs.wordlift.io/problems/invalid_span", "Invalid span", "Invalid span", undefined]);
  assert.deepEqual(problemOf(500, { error: "boom" }).detail, { error: "boom" });      // no detail, no title: the body
  assert.equal(problemOf(502, "<html>bad gateway</html>", new Headers({ "Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT" })).retryAfter, undefined);
  assert.equal(problemOf(503, null, new Headers({ "Retry-After": " 7 " })).retryAfter, 7);
  assert.equal(problemOf(503, [1, 2]).detail[0], 1);
  assert.ok(failure({ status: 418, body: "teapot" }) instanceof ResolverUnavailableError);
});

test("credits are read from the header when it is a number", async () => {
  const ok = { mentions: [], dataset_uri: "wikidata://public", language: "en", processing_time_ms: 1 };
  const withCredits = async () => new Response(JSON.stringify(ok), { status: 200, headers: { "X-Wordlift-Consumption": "6" } });
  assert.equal((await new WordLiftResolver({ apiKey: "k", accountId: "1", fetch: withCredits }).resolve({ text: TEXT })).credits, 6);
  const junk = async () => new Response(JSON.stringify(ok), { status: 200, headers: { "X-Wordlift-Consumption": "many" } });
  assert.equal((await new WordLiftResolver({ apiKey: "k", accountId: "1", fetch: junk }).resolve({ text: TEXT })).credits, undefined);
});
