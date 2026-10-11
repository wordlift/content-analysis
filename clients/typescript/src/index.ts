/**
 * WordLift resolve() client: map mentions to identities in an explicit
 * dataset, or get `unresolved`. Zero dependencies; uses the global `fetch`
 * (Node 18+, browsers, edge runtimes).
 *
 *   const wl = new WordLiftResolver({ apiKey: process.env.WL_KEY! });
 *   const res = await wl.resolve({
 *     text: "Apple opened a new store in Rome.",
 *     language: "en",
 *     mentions: [{ text: "Apple", start: 0, end: 5,
 *                  candidates: [{ id: "wd:Q312" }, { id: "wd:Q89" }] }],
 *   });
 *   res.mentions[0].status   // "resolved" | "unresolved"
 *
 * `unresolved` is a result, not an error. A transport failure throws
 * `ResolverUnavailableError`, so a network problem never becomes a guessed
 * identity; catch it and treat the mention as unresolved.
 */

/** The WordLift API: requests go to `POST {baseUrl}/accounts/{accountId}/resolve`, metered against the account. */
export const DEFAULT_BASE_URL = "https://api.wordlift.io";
/** The engine's own address, before the gateway; still answers, not metered. Pass it as `endpoint` to call the engine directly. */
export const ENGINE_ENDPOINT = "https://resolve.wordlift.io/v1/resolve";
export const PUBLIC_DATASET = "wikidata://public";

/** Identity in the dataset: "Q312", "wd:Q312" or a Wikidata entity URI. Echoed back in the same form. */
export interface Candidate {
  id: string;
  label?: string;
  description?: string;
  types?: string[];
  /** Your retrieval score; informational. */
  score?: number;
}

export interface Mention {
  text: string;
  start: number;
  end: number;
  /** Type hint, e.g. "Person", "Organization". */
  type?: string;
  /** Omit to let the engine retrieve against the dataset. */
  candidates?: Candidate[];
}

/** An entity of your own vocabulary or graph. `id` is echoed back as given. */
export interface DecisionSignals {
  /** "fast" (exact dictionary hit, model skipped), "reranked" (public world), "vocabulary" (your dataset). */
  path: "fast" | "reranked" | "vocabulary" | string;
  /** The dictionary's P(entity | surface form) on the public world; null for a user dataset. */
  retrieval_prior?: number | null;
  /** The model's match probability for the winning pair; null when the model was skipped. */
  match_probability?: number | null;
  /** True when the dictionary rescue, not the model, accepted the link. */
  rescued: boolean;
}

export interface UserEntity {
  id: string;
  name: string;
  aliases?: string[];
  description?: string;
  /** schema.org types, e.g. "Organization", "Person", "Place". */
  types?: string[];
  same_as?: string[];
}

export interface ResolveRequest {
  text: string;
  /** ISO 639-1; detected when omitted. */
  language?: string;
  /** Omit to let the engine detect mentions. */
  mentions?: Mention[];
  /**
   * "wikidata://public" (default), "wordlift://dataset/me" (your WordLift graph,
   * read by the engine with your key), or "inline" together with `dataset`.
   * A comma-separated list is tried in order per mention:
   * "wordlift://dataset/me,wikidata://public" = your entities first, Wikidata for the rest.
   */
  dataset_uri?: string;
  /** Inline vocabulary (up to 5000 entities): the answer is one of these ids or unresolved. */
  dataset?: { entities: UserEntity[] };
  include?: Array<"candidates" | "evidence">;
  /** NER threshold when the engine detects mentions (0..1). */
  confidence?: number;
}

export interface ResolvedEntity {
  id: string;
  label: string;
  description?: string | null;
  /** Absent when the engine has no type for the entity. */
  types?: string[];
  same_as?: string[];
}

export type UnresolvedReason =
  | "no_suitable_candidate"
  | "no_candidates"
  | "type_conflict"
  | "low_relevance";

export interface MentionResolution {
  text: string;
  start: number;
  end: number;
  type?: string | null;
  status: "resolved" | "unresolved";
  /** The world that answered this mention, e.g. "wordlift://dataset/me" or "wikidata://public". */
  dataset_uri?: string | null;
  entity: ResolvedEntity | null;
  /** Relevance of the returned entity in [0, 1]; not a calibrated probability. */
  score?: number | null;
  reason?: UnresolvedReason | string | null;
  /** How the decision was reached; always present when the engine ranked candidates. */
  signals?: DecisionSignals | null;
  candidates?: Array<Record<string, unknown>> | null;
  evidence?: Record<string, unknown> | null;
}

export interface ResolveResponse {
  mentions: MentionResolution[];
  dataset_uri: string;
  language: string;
  processing_time_ms: number;
  engine?: string;
  /** Smart credits this request cost (`X-Wordlift-Consumption`), when the header was present. */
  credits?: number;
}

export interface WordLiftResolverOptions {
  /** WordLift API key, sent as `Authorization: Key <apiKey>`. */
  apiKey: string;
  /** The WordLift API; default https://api.wordlift.io. */
  baseUrl?: string;
  /** Your account id (the `accountId` of GET /accounts/me). Read from the key on first use when omitted. */
  accountId?: string | number;
  /** Send every request to this URL as given, for example ENGINE_ENDPOINT; skips the account lookup. */
  endpoint?: string;
  datasetUri?: string;
  /** Milliseconds; default 30000. */
  timeoutMs?: number;
  /** Override for tests or custom runtimes. */
  fetch?: typeof fetch;
}

/**
 * What an error response says, read once: the RFC 9457 Problem JSON members
 * when the engine sent them (`code` is the stable identifier, `type` its page
 * under https://docs.wordlift.io/problems/), the raw body otherwise, and the
 * `Retry-After` header in seconds when there is one. The gateway's allowance
 * block is a 429 with a text/plain body, so it has no `code`.
 */
export interface Problem {
  status: number;
  code?: string;
  type?: string;
  title?: string;
  detail?: unknown;
  retryAfter?: number;
  body?: unknown;
}

export function problemOf(status: number, body: unknown, headers?: Headers): Problem {
  const problem: Problem = { status, body };
  if (typeof body === "object" && body !== null && !Array.isArray(body)) {
    const b = body as Record<string, unknown>;
    if (typeof b.code === "string") problem.code = b.code;
    if (typeof b.type === "string") problem.type = b.type;
    if (typeof b.title === "string") problem.title = b.title;
    problem.detail = b.detail ?? b.title ?? body;
  } else {
    problem.detail = body;
  }
  const retry = headers?.get("Retry-After");
  if (retry !== null && retry !== undefined && /^\d+$/.test(retry.trim())) problem.retryAfter = Number(retry);
  return problem;
}

/** The engine could not be reached or answered with an error status it could not act on. */
export class ResolverUnavailableError extends Error {
  readonly status?: number;
  readonly body?: unknown;
  /** The problem `code` when the body was Problem JSON. */
  readonly code?: string;
  /** Seconds from the `Retry-After` header, when given. */
  readonly retryAfter?: number;
  constructor(message: string, status?: number, body?: unknown, problem?: Problem) {
    super(message);
    this.name = "ResolverUnavailableError";
    this.status = status;
    this.body = body;
    this.code = problem?.code;
    this.retryAfter = problem?.retryAfter;
  }
}

/**
 * The request was refused with 429: the replica's bounded queue is full (the
 * engine's Problem JSON `too_many_requests`, with `Retry-After`) or the
 * account's monthly allowance is spent (the gateway's text/plain body with
 * the `X-RateLimit-*` headers, no `code`). Nothing was resolved or charged.
 * Wait `retryAfter` seconds and send the request again; the client does not
 * retry on its own.
 */
export class RateLimitedError extends Error {
  readonly status = 429;
  readonly code?: string;
  readonly retryAfter?: number;
  readonly body?: unknown;
  constructor(problem: Problem) {
    super(problem.retryAfter !== undefined ? `resolve() rate limited; retry after ${problem.retryAfter}s` : "resolve() rate limited");
    this.name = "RateLimitedError";
    this.code = problem.code;
    this.retryAfter = problem.retryAfter;
    this.body = problem.body;
  }
}

/** The engine refused the key (401 or 403): no mention of this request can be resolved. */
export class AuthorizationError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly detail: unknown;
  constructor(problem: Problem) {
    super(`resolve() refused the key: HTTP ${problem.status}`);
    this.name = "AuthorizationError";
    this.status = problem.status;
    this.code = problem.code;
    this.detail = problem.detail;
  }
}

/**
 * The engine answered, but not with something a client can act on: a body that
 * is not the contract's shape, no row for the requested span, two rows for it,
 * a resolved row without an identity. Distinct from a transport failure and
 * from a genuine `unresolved`; never turned into an identity.
 */
export class ResolverProtocolError extends Error {
  readonly body?: unknown;
  constructor(message: string, body?: unknown) {
    super(message);
    this.name = "ResolverProtocolError";
    this.body = body;
  }
}

/**
 * The request itself was rejected (422): bad span, unknown identity form,
 * unsupported dataset. `code` is the stable problem code (`invalid_span`,
 * `invalid_identity`, `dataset_not_supported`, ...) and `detail` the sentence
 * or the validation errors.
 */
export class InvalidRequestError extends Error {
  readonly detail: unknown;
  readonly code?: string;
  readonly problem?: Problem;
  constructor(detail: unknown, problem?: Problem) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.name = "InvalidRequestError";
    this.detail = detail;
    this.code = problem?.code;
    this.problem = problem;
  }
}

/** A mention whose span is empty or does not hold its text; raised before any request. */
export class InvalidMentionError extends RangeError {
  readonly mention: Mention;
  constructor(mention: Mention) {
    super(`mention ${JSON.stringify(mention.text)} [${mention.start}, ${mention.end}) does not lie in the text as written`);
    this.name = "InvalidMentionError";
    this.mention = mention;
  }
}

/** The answer for one document: one row per mention, in the order given, with document offsets. */
export interface DocumentResolution {
  mentions: MentionResolution[];
  /** Requests sent: one per 200 mentions and per 100,000 characters. */
  requests: number;
  /** Smart credits charged, summed over the requests (`X-Wordlift-Consumption`), when every request reported them. */
  credits?: number;
}

/**
 * Character ranges that cover `text`, each at most `limit` long, none cutting a
 * mention. A chunk ends at the last newline or space before the limit, and
 * earlier when that would fall inside a mention.
 */
export function chunkDocument(text: string, mentions: Mention[], limit: number): Array<[number, number]> {
  if (text.length <= limit) return [[0, text.length]];
  const spans = mentions.map((m) => [m.start, m.end] as const).sort((a, b) => a[0] - b[0]);
  const chunks: Array<[number, number]> = [];
  let start = 0;
  while (start < text.length) {
    let end = Math.min(text.length, start + limit);
    if (end < text.length) {
      const cut = Math.max(text.lastIndexOf("\n", end - 1), text.lastIndexOf(" ", end - 1));
      if (cut > start) end = cut;
      for (const [ms, me] of spans) {              // never cut through a mention
        if (ms < end && end < me) {
          const back = text.lastIndexOf(" ", ms - 1);
          // step back before the mention; a mention that starts the chunk and is
          // longer than the limit is sent whole (the engine refuses it with 422)
          end = back > start ? back : ms > start ? ms : me;
          break;
        }
      }
    }
    chunks.push([start, end]);
    start = end;
  }
  return chunks;
}

/** Status to error: 422 and 401/403 are the caller's, 429 is a wait, the rest is unavailability. */
export function failure(problem: Problem): Error {
  if (problem.status === 422) return new InvalidRequestError(problem.detail, problem);
  if (problem.status === 401 || problem.status === 403) return new AuthorizationError(problem);
  if (problem.status === 429) return new RateLimitedError(problem);
  return new ResolverUnavailableError(`resolve() HTTP ${problem.status}`, problem.status, problem.body, problem);
}

export class WordLiftResolver {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly datasetUri: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;
  private readonly fixedEndpoint?: string;
  private _accountId?: string;
  private endpointPromise?: Promise<string>;

  constructor(options: WordLiftResolverOptions) {
    if (!options.apiKey) throw new Error("apiKey is required");
    this.apiKey = options.apiKey;
    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.datasetUri = options.datasetUri ?? PUBLIC_DATASET;
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.fetchImpl = options.fetch ?? globalThis.fetch;
    if (!this.fetchImpl) throw new Error("no fetch available; pass options.fetch");
    this.fixedEndpoint = options.endpoint;
    if (options.accountId !== undefined) this._accountId = String(options.accountId);
  }

  /** The account id in use, once known (given, or read from the key by the first request). */
  get accountId(): string | undefined {
    return this._accountId;
  }

  /**
   * Where requests go: `endpoint` as given, or `{baseUrl}/accounts/{accountId}/resolve`
   * with the account id read from the key (GET /accounts/me) on first use and kept.
   * A refused key is an AuthorizationError, an unreachable API a ResolverUnavailableError,
   * an answer without `accountId` (wrong baseUrl) a ResolverProtocolError.
   */
  endpoint(): Promise<string> {
    if (this.fixedEndpoint) return Promise.resolve(this.fixedEndpoint);
    if (!this.endpointPromise) {
      this.endpointPromise = this.discover().catch((err) => { this.endpointPromise = undefined; throw err; });
    }
    return this.endpointPromise;
  }

  private async discover(): Promise<string> {
    if (this._accountId === undefined) {
      const { response, parsed } = await this.send(`${this.baseUrl}/accounts/me`, { method: "GET" });
      if (!response.ok) throw failure(problemOf(response.status, parsed, response.headers));
      const account = (parsed as { accountId?: unknown } | null)?.accountId;
      if (typeof account !== "number" && typeof account !== "string") {
        throw new ResolverProtocolError(`${this.baseUrl}/accounts/me did not return an accountId`, parsed);
      }
      this._accountId = String(account);
    }
    return `${this.baseUrl}/accounts/${this._accountId}/resolve`;
  }

  /** One HTTP exchange with the key header and the timeout; a transport failure is a ResolverUnavailableError. */
  private async send(url: string, init: { method: string; body?: string }): Promise<{ response: Response; parsed: unknown }> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let response: Response;
    let text: string;
    try {
      response = await this.fetchImpl(url, {
        ...init,
        headers: { Authorization: `Key ${this.apiKey}`, "Content-Type": "application/json" },
        signal: controller.signal,
      });
      // The timeout covers the body too: a stalled stream is as unavailable as a stalled connection.
      text = await response.text();
    } catch (err) {
      throw new ResolverUnavailableError(`resolve() unreachable: ${(err as Error).message}`);
    } finally {
      clearTimeout(timer);
    }
    let parsed: unknown = text;
    try { parsed = text ? JSON.parse(text) : null; } catch { /* keep raw text */ }
    return { response, parsed };
  }

  /** POST the request as given (the contract's shape) to the account's resolve route. */
  async resolve(request: ResolveRequest): Promise<ResolveResponse> {
    const body = { dataset_uri: this.datasetUri, ...request };
    const url = await this.endpoint();
    const { response, parsed } = await this.send(url, { method: "POST", body: JSON.stringify(body) });
    if (!response.ok) throw failure(problemOf(response.status, parsed, response.headers));
    const result = validateResponse(parsed);
    const credits = response.headers.get("X-Wordlift-Consumption");
    if (credits !== null && /^\d+(\.\d+)?$/.test(credits.trim())) result.credits = Number(credits);
    return result;
  }

  /** Mentions per request; the engine resolves a document's mentions in one pass. */
  static MAX_MENTIONS_PER_REQUEST = 200;
  /** The engine's limit on `text`; a longer document is sent in chunks that never cut a mention. */
  static MAX_TEXT_CHARS = 100_000;

  /**
   * Resolve all `mentions` of one document with as few requests as possible.
   *
   * Every span is checked first: a mention whose `[start, end)` does not hold
   * its `text` throws InvalidMentionError before any request is sent. Then one
   * request carries up to MAX_MENTIONS_PER_REQUEST spans and, with an inline
   * `dataset`, the vocabulary once; a document over MAX_TEXT_CHARS is cut into
   * chunks that never split a mention (a mention longer than the limit is sent
   * whole and refused by the engine), with spans rebased to the chunk and
   * answers rebased back to document offsets. Rows come back in the order of
   * `mentions`; a span with no row or two rows is a ResolverProtocolError.
   * The engine decides each mention on the 400 characters either side of it,
   * so one request per document gives the same decisions as one per mention.
   * Errors are those of resolve(); the client never retries on its own, since
   * a timed-out request may have been served and metered.
   */
  async resolveDocument(
    text: string,
    mentions: Mention[],
    options: Omit<ResolveRequest, "text" | "mentions"> = {},
  ): Promise<DocumentResolution> {
    for (const m of mentions) {
      if (m.start < 0 || m.end <= m.start || m.end > text.length || text.slice(m.start, m.end) !== m.text) throw new InvalidMentionError(m);
    }
    const out: Array<MentionResolution | undefined> = new Array(mentions.length);
    let requests = 0;
    let credits: number | undefined = 0;
    const perRequest = WordLiftResolver.MAX_MENTIONS_PER_REQUEST;
    for (const [cStart, cEnd] of chunkDocument(text, mentions, WordLiftResolver.MAX_TEXT_CHARS)) {
      const inside = mentions.map((m, i) => i).filter((i) => cStart <= mentions[i].start && mentions[i].end <= cEnd);
      for (let b = 0; b < inside.length; b += perRequest) {
        const batch = inside.slice(b, b + perRequest);
        const res = await this.resolve({
          ...options,
          text: text.slice(cStart, cEnd),
          mentions: batch.map((i) => ({ ...mentions[i], start: mentions[i].start - cStart, end: mentions[i].end - cStart })),
        });
        requests += 1;
        credits = credits !== undefined && res.credits !== undefined ? credits + res.credits : undefined;
        for (const i of batch) {
          const local = [mentions[i].start - cStart, mentions[i].end - cStart];
          const hits = res.mentions.filter((r) => r.start === local[0] && r.end === local[1]);
          if (hits.length !== 1) {
            throw new ResolverProtocolError(`resolve() returned ${hits.length} rows for span [${mentions[i].start}, ${mentions[i].end})`, res);
          }
          out[i] = { ...hits[0], start: mentions[i].start, end: mentions[i].end };
        }
      }
    }
    // chunks cover the text and none ends inside a mention, so every mention was sent exactly once
    const result: DocumentResolution = { mentions: out as MentionResolution[], requests };
    if (credits !== undefined) result.credits = credits;
    return result;
  }

  /**
   * Convenience: one mention, your candidates, one answer.
   *
   * The answer is the row with exactly the requested span. No such row, or
   * more than one, is a ResolverProtocolError: another row is never the
   * answer for this mention.
   */
  async resolveMention(
    text: string,
    mention: Mention,
    options: { language?: string; include?: ResolveRequest["include"] } = {},
  ): Promise<MentionResolution> {
    const res = await this.resolve({ text, mentions: [mention], ...options });
    const hits = res.mentions.filter((m) => m.start === mention.start && m.end === mention.end);
    if (hits.length !== 1) {
      throw new ResolverProtocolError(`resolve() returned ${hits.length} rows for span [${mention.start}, ${mention.end})`, res);
    }
    return hits[0];
  }
}

/**
 * The structural invariants a client needs for identity safety, checked on
 * every successful response; unknown fields pass through. A body that fails
 * is a ResolverProtocolError.
 */
export function validateResponse(parsed: unknown): ResolveResponse {
  const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
  if (!isObject(parsed) || !Array.isArray(parsed.mentions)) {
    throw new ResolverProtocolError("resolve() answered without a `mentions` list", parsed);
  }
  for (const row of parsed.mentions) {
    if (!isObject(row) || typeof row.start !== "number" || typeof row.end !== "number") {
      throw new ResolverProtocolError("resolve() returned a mention without a span", parsed);
    }
    if (row.status === "resolved") {
      const entity = row.entity;
      if (!isObject(entity) || typeof entity.id !== "string" || entity.id === "") {
        throw new ResolverProtocolError("resolve() returned a resolved mention without an identity", parsed);
      }
    } else if (row.status !== "unresolved") {
      throw new ResolverProtocolError(`resolve() returned an unknown status ${String(row.status)}`, parsed);
    }
  }
  return parsed as unknown as ResolveResponse;
}
