# `resolve()` contract — draft

The contract has one job:

> map language to an identity in an explicit dataset, or return unresolved.

`unresolved` is a valid resolution result, not an exception. The basic API
returns the resolver's decision; callers should not need to invent a threshold
to decide whether an identity exists.

## Request

```http
POST /v1/resolve
Authorization: Key <your_wordlift_key>
Content-Type: application/json
```

```json
{
  "text": "Apple opened a new store in Rome.",
  "dataset_uri": "wikidata://public",
  "language": "en",
  "mentions": [
    {
      "text": "Apple", "start": 0, "end": 5,
      "candidates": [
        {"id": "wd:Q312", "label": "Apple Inc.", "description": "American technology company", "score": 0.93},
        {"id": "wd:Q89", "label": "apple", "description": "fruit of the apple tree", "score": 0.41}
      ]
    }
  ]
}
```

| field | required | meaning |
|---|---:|---|
| `text` | yes | source text containing the mention(s); the resolver reads it as context |
| `dataset_uri` | no | semantic world the identities belong to; defaults to the public dataset |
| `language` | no | ISO 639-1 code; auto-detected if omitted |
| `mentions` | no | explicit spans; if omitted the engine detects mentions |
| `mentions[].candidates` | no | identities the caller retrieved from its own graph or vocabulary; if omitted the engine retrieves against `dataset_uri` |
| `include` | no | diagnostics such as `candidates` or `evidence` |

Spans, not strings, identify a mention so repeated surface forms are
unambiguous. Bring-your-own candidates is the shape the open pipeline uses:
the caller's graph defines the world, retrieval introduces its entities, and
`resolve()` decides whether the mention refers to one of them.

## Resolved response

```json
{
  "mentions": [
    {
      "text": "Apple", "start": 0, "end": 5,
      "status": "resolved",
      "entity": {
        "id": "wd:Q312",
        "label": "Apple Inc.",
        "types": ["Organization"],
        "same_as": ["https://www.wikidata.org/entity/Q312"]
      },
      "score": 0.97
    }
  ],
  "dataset_uri": "wikidata://public"
}
```

`score` is the resolver's decision score. The contract does not call it a
probability or a calibrated confidence unless calibration is demonstrated for
the selected dataset and operating condition.

## Unresolved response

```json
{
  "mentions": [
    {
      "text": "Acme Neural Fabric", "start": 0, "end": 18,
      "status": "unresolved",
      "entity": null,
      "reason": "no_suitable_candidate"
    }
  ],
  "dataset_uri": "wikidata://public"
}
```

Reason vocabulary (developer-facing categories, not a commitment to expose the
engine's internal abstention rules):

- `no_suitable_candidate`
- `no_candidates`
- `type_conflict`
- `low_relevance`

The client adds one local reason, `resolver_unavailable`, when the service
cannot be reached: a transport failure never turns into a guessed identity.

## Roadmap: `score` and `signals`

Every mention the engine ranked carries a compact `signals` block:

```json
{"score": 0.93,
 "signals": {"path": "reranked", "retrieval_prior": 0.81, "match_probability": 0.93, "rescued": false}}
```

`path` is `fast` (an exact dictionary hit that dominated, model skipped),
`reranked` (public world, model scored) or `vocabulary` (your dataset).
`retrieval_prior` is the dictionary's P(entity | surface form) on the public
world, absent for a user dataset. `match_probability` is the model's score
for the winning pair, absent when the model was skipped. `rescued` is true
when the dictionary, not the model, accepted the link.

`score` today is the relevance of the returned entity in [0, 1]: a prior-derived
band on the fast path, the model's probability elsewhere, never below 0.10
where the model ran. It is not one metric across paths yet. The contract
commitment: `score` will come to mean one thing, confidence in the final
decision, calibrated on held-out data, when the Resolve decision layer ships;
the technical numbers stay in `signals`. Until then, read `signals.path`
before comparing scores across mentions.

## Optional diagnostics

`"include": ["candidates", "evidence"]` may expose the bounded candidate world
and provenance for debugging while the default response stays small.

`evidence` carries `engine_reason` and `signals`: the path that decided
(`fast`, `reranked`, `vocabulary`), the winning candidate, its prior and
source, the model's raw match probability, the KB and mention types, the link
threshold, whether the dictionary rescue applied, and the decision reason. The
response then also carries `timings`, milliseconds per stage. `score` is a
relevance score in [0, 1], not a calibrated probability; a resolved answer
always carries at least 0.10 on paths that consult the model.

## Dataset semantics

`dataset_uri` is broader than a vocabulary. A dataset may provide canonical
identifiers, labels and aliases, definitions, types, ontology constraints and
public identity mappings (Wikidata, DBpedia, Wikipedia).

Supported today:

| `dataset_uri` | Identities | Notes |
|---|---|---|
| `wikidata://public` | Wikidata QIDs | default; per-mention `candidates` allowed |
| `wordlift://dataset/me` | IRIs of the caller's WordLift knowledge graph | read with the caller's key: named, non-content entities (`schema:name`, `alternateName`, `description`, `rdf:type`, `sameAs`); cached briefly per key |
| `inline` | the ids in `dataset.entities` | `{"entities": [{"id", "name", "aliases", "description", "types", "same_as"}]}`, at most 5,000 entities; `dataset_uri` may be omitted when `dataset` is present |

`dataset_uri` may be an ordered, comma-separated list of worlds, tried per
mention: `wordlift://dataset/me,wikidata://public` answers with the caller's
IRI where the graph knows the entity and with a Wikidata QID otherwise; each
mention reports the world that answered it in its own `dataset_uri`.

With a user dataset, retrieval is a normalized exact match on names and
aliases and the dataset itself is the candidate world, so per-mention
`candidates` are rejected (422). Every match still goes through the engine's
context ranking and abstention: `unresolved` with `no_candidates` when nothing
in the dataset carries that surface form, `low_relevance` when the context does
not support the match. Resolved entities carry the dataset's own `id`, `label`,
`types` and `same_as` links.

## Out of scope

Relation extraction, automatic graph writes, graph traversal, and any exposure
of the engine's ranking, boosting or threshold knobs. Existing graph
relationships may later be returned by a separate `expand()` operation.

## Validation

The engine is promoted per language only after a frozen, held-out gate answers
two questions: when the correct entity is present, is resolution preserved;
and when it is removed, does the resolver return `unresolved` rather than the
least-wrong candidate. The key failure metric is the **false resolution rate**
under forced NIL. Candidate recall is reported separately from resolution
accuracy so every error has an owner.
