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

export const DEFAULT_BASE_URL = "https://resolve.wordlift.io";
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
  types: string[];
  same_as: string[];
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
  engine: string;
}

export interface WordLiftResolverOptions {
  /** WordLift API key, sent as `Authorization: Key <apiKey>`. */
  apiKey: string;
  baseUrl?: string;
  datasetUri?: string;
  /** Milliseconds; default 30000. */
  timeoutMs?: number;
  /** Override for tests or custom runtimes. */
  fetch?: typeof fetch;
}

/** The engine could not be reached or answered with an error status. */
export class ResolverUnavailableError extends Error {
  readonly status?: number;
  readonly body?: unknown;
  constructor(message: string, status?: number, body?: unknown) {
    super(message);
    this.name = "ResolverUnavailableError";
    this.status = status;
    this.body = body;
  }
}

/** The request itself was rejected (422): bad span, unknown identity form, unsupported dataset. */
export class InvalidRequestError extends Error {
  readonly detail: unknown;
  constructor(detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.name = "InvalidRequestError";
    this.detail = detail;
  }
}

export class WordLiftResolver {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly datasetUri: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(options: WordLiftResolverOptions) {
    if (!options.apiKey) throw new Error("apiKey is required");
    this.apiKey = options.apiKey;
    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.datasetUri = options.datasetUri ?? PUBLIC_DATASET;
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.fetchImpl = options.fetch ?? globalThis.fetch;
    if (!this.fetchImpl) throw new Error("no fetch available; pass options.fetch");
  }

  /** POST /v1/resolve with the request as given (the contract's shape). */
  async resolve(request: ResolveRequest): Promise<ResolveResponse> {
    const body = { dataset_uri: this.datasetUri, ...request };
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}/v1/resolve`, {
        method: "POST",
        headers: { Authorization: `Key ${this.apiKey}`, "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (err) {
      throw new ResolverUnavailableError(`resolve() unreachable: ${(err as Error).message}`);
    } finally {
      clearTimeout(timer);
    }
    const text = await response.text();
    let parsed: unknown = text;
    try { parsed = text ? JSON.parse(text) : null; } catch { /* keep raw text */ }
    if (response.status === 422) throw new InvalidRequestError((parsed as { detail?: unknown })?.detail ?? parsed);
    if (!response.ok) throw new ResolverUnavailableError(`resolve() HTTP ${response.status}`, response.status, parsed);
    return parsed as ResolveResponse;
  }

  /** Convenience: one mention, your candidates, one answer. */
  async resolveMention(
    text: string,
    mention: Mention,
    options: { language?: string; include?: ResolveRequest["include"] } = {},
  ): Promise<MentionResolution> {
    const res = await this.resolve({ text, mentions: [mention], ...options });
    const hit = res.mentions.find((m) => m.start === mention.start && m.end === mention.end) ?? res.mentions[0];
    if (!hit) throw new ResolverUnavailableError("resolve() returned no mention");
    return hit;
  }
}
