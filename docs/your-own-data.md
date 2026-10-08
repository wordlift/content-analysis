# Run resolve() on your own data

`resolve()` answers one question per mention: which identity in a dataset does
this mention refer to, or `unresolved`. By default the dataset is Wikidata.
This guide is about making the dataset *yours*: your knowledge graph, your
vocabulary, your ids, with Wikidata as the fallback when you want one.

Three ways in, all through the same `POST /v1/resolve` call:

| You have | Send | You get back |
|---|---|---|
| A WordLift knowledge graph | `"dataset_uri": "wordlift://dataset/me"` | your graph's IRIs |
| A list of entities in JSON | `"dataset": {"entities": [...]}` | the ids you sent |
| Either of the above, plus Wikidata for everything else | `"dataset_uri": "wordlift://dataset/me,wikidata://public"` | your IRI where your graph knows the entity, a QID elsewhere |

Nothing is uploaded or trained. The engine reads the dataset per request
(your graph is cached for a few minutes), matches surface forms against the
names and aliases in it, and runs every match through the same context
ranking and abstention gate as Wikidata. `unresolved` stays a valid answer.

## 1. Your WordLift knowledge graph, derived from the key

If you use WordLift, your graph is already the dataset. There is nothing to
configure: the key in the `Authorization` header identifies the graph.

```bash
curl -s -X POST https://resolve.wordlift.io/v1/resolve \
  -H "Authorization: Key $WL_KEY" -H "Content-Type: application/json" \
  -d '{"text": "Andrea Volpini founded WordLift in Rome.",
       "language": "en",
       "dataset_uri": "wordlift://dataset/me"}'
```

```json
{"mentions": [
  {"text": "Andrea Volpini", "start": 0, "end": 14, "status": "resolved",
   "dataset_uri": "wordlift://dataset/me",
   "entity": {"id": "http://data.wordlift.io/wl0216/entity/andrea_volpini",
              "label": "Andrea Volpini", "types": ["Person"],
              "same_as": ["https://www.wikidata.org/wiki/Q28085380", "https://www.researchgate.net/profile/Andrea-Volpini"]},
   "score": 0.99,
   "signals": {"path": "vocabulary", "retrieval_prior": null, "match_probability": 0.99, "rescued": false}},
  {"text": "WordLift", "start": 27, "end": 35, "status": "resolved",
   "dataset_uri": "wordlift://dataset/me",
   "entity": {"id": "http://data.wordlift.io/wl0216/entity/wordlift",
              "label": "WordLift", "types": ["Organization"],
              "same_as": ["http://dbpedia.org/resource/WordLift", "http://www.wikidata.org/entity/Q31998763"]},
   "score": 0.99},
  {"text": "Rome", "start": 39, "end": 43, "status": "resolved",
   "dataset_uri": "wordlift://dataset/me",
   "entity": {"id": "http://data.wordlift.io/wl0216/entity/roma", "label": "Roma", "types": ["Place"],
              "same_as": ["http://www.wikidata.org/entity/Q220", "http://dbpedia.org/resource/Rome"]},
   "score": 0.98}],
 "dataset_uri": "wordlift://dataset/me", "language": "en"}
```

What the engine reads from the graph: every entity with a `schema:name`; its
surface forms from `alternateName`, `rdfs:label`, `skos:prefLabel`,
`skos:altLabel` and `skos:hiddenLabel`; its `description`, or
`skos:definition` / `rdfs:comment` when there is none (the passage the ranker
compares with the context); `rdf:type` and `sameAs`. Content items (articles,
web pages) are skipped unless the graph itself annotates other content with
them (`schema:mentions` / `schema:about`), which is how WordLift models topic
pages. The first call on a graph takes a few seconds while it is read; later
calls reuse it for about ten minutes.

Python:

```python
from resolve_pipeline import WordLiftResolver, run

resolver = WordLiftResolver(api_key=KEY, dataset_uri=WordLiftResolver.WORDLIFT_GRAPH)
for r in run("Andrea Volpini founded WordLift in Rome.", resolver=resolver, language="en"):
    print(r.mention.text, "->", r.entity.id if r.resolved else f"unresolved ({r.reason})")
```

TypeScript:

```ts
import { WordLiftResolver } from "@wordlift/resolve";

const resolver = new WordLiftResolver({ apiKey: KEY, datasetUri: "wordlift://dataset/me" });
const res = await resolver.resolve({ text: "Andrea Volpini founded WordLift in Rome.", language: "en" });
for (const m of res.mentions) console.log(m.text, m.status, m.entity?.id ?? m.reason);
```

Command line:

```bash
python -m resolve_pipeline "Andrea Volpini founded WordLift in Rome." --dataset-uri wordlift://dataset/me
```

## 2. A vocabulary in JSON, sent with the request

No WordLift graph? Send the entities inline. Any ids, any source: a product
catalogue, a taxonomy, a CRM export, a SKOS vocabulary flattened to JSON.

```json
{"entities": [
  {"id": "https://acme.example/kg/acme",
   "name": "Acme",
   "aliases": ["ACME Corp", "Acme Corporation"],
   "description": "Manufacturer of rocket-powered products for coyotes",
   "types": ["Organization"],
   "same_as": ["https://www.wikidata.org/entity/Q1319384"]},
  {"id": "https://acme.example/kg/rome-office",
   "name": "Rome",
   "description": "Acme's southern European office in Rome, Italy",
   "types": ["Place"]}
]}
```

| field | required | what it does |
|---|---:|---|
| `id` | yes | returned as `entity.id`, unchanged; any string, IRIs recommended |
| `name` | yes | the surface form matched in text, case-insensitive |
| `aliases` | no | more surface forms: abbreviations, inflections, the other spelling. The ranker reads them too ("also called …"), which measurably helps |
| `description` | no | one line the ranker compares with the mention's context; the single most useful field when a name is ambiguous |
| `types` | no | schema.org or plain type names; returned on the answer, and checked against a mention `type` when you pass one |
| `same_as` | no | public identities (Wikidata, DBpedia, your CRM); returned on the answer and used by the bridge in section 3 |

Up to 5,000 entities per request. `dataset_uri` may be omitted when
`dataset` is present: it is `inline`.

```bash
curl -s -X POST https://resolve.wordlift.io/v1/resolve \
  -H "Authorization: Key $WL_KEY" -H "Content-Type: application/json" \
  -d @- <<'JSON'
{"text": "ACME Corp opened an office in Rome.",
 "language": "en",
 "dataset": {"entities": [
   {"id": "https://acme.example/kg/acme", "name": "Acme", "aliases": ["ACME Corp"], "types": ["Organization"]},
   {"id": "https://acme.example/kg/rome-office", "name": "Rome", "description": "Acme's office in Rome", "types": ["Place"]}]}}
JSON
```

Python and TypeScript take the same list:

```python
resolver = WordLiftResolver(api_key=KEY, dataset=[
    {"id": "https://acme.example/kg/acme", "name": "Acme", "aliases": ["ACME Corp"], "types": ["Organization"]},
    {"id": "https://acme.example/kg/rome-office", "name": "Rome", "description": "Acme's office in Rome", "types": ["Place"]},
])
```

```ts
const res = await resolver.resolve({ text, language: "en",
  dataset: { entities: [{ id: "https://acme.example/kg/acme", name: "Acme", aliases: ["ACME Corp"], types: ["Organization"] }] } });
```

```bash
python -m resolve_pipeline "ACME Corp opened an office in Rome." --dataset my-entities.json
```

The answer is always one of your ids or `unresolved`. A mention that matches
none of your names or aliases is `unresolved` with `no_candidates`; a match
whose context does not fit the entity is `low_relevance`.

## 3. Local entity first, Wikidata for the rest

Most graphs know a few hundred entities that matter to you and none of the
rest. Give `dataset_uri` an ordered list and each mention is tried world by
world: your graph first, then Wikidata.

```json
{"text": "Andrea Volpini and David Riccitelli founded WordLift in Rome; the company works with Google and the W3C.",
 "language": "en",
 "dataset_uri": "wordlift://dataset/me,wikidata://public"}
```

Each mention reports the world that answered it in its own `dataset_uri`:

| mention | answered by | entity |
|---|---|---|
| Andrea Volpini | `wordlift://dataset/me` | your IRI, with Wikidata Q28085380 in `same_as` |
| WordLift | `wordlift://dataset/me` | your IRI, with DBpedia and Wikidata Q31998763 in `same_as` |
| David Riccitelli | `wikidata://public` | `unresolved`, `low_relevance`: not in the graph and no supported Wikidata match, so no guess |
| Google | `wordlift://dataset/me` | your IRI |

**The sameAs bridge.** When Wikidata answers a mention with a QID that one of
your entities lists in `same_as`, you get your entity back instead: your id,
your label and types, and `same_as` merged with the Wikidata and DBpedia
identities. So a mention written differently from the name in your graph
still lands on your IRI whenever the graph links it to the public one. This
is why `same_as` is worth filling in an inline vocabulary as well.

The same ordering works with an inline vocabulary: `"dataset_uri": "inline,wikidata://public"` with `dataset` present.

## Reading the answer

- `status` is `resolved` or `unresolved`. Unresolved is a result. Its `reason` is `no_candidates` (nothing in the dataset carries that surface form), `low_relevance` (a name matched but the context did not support it), `no_suitable_candidate` or `type_conflict`.
- `dataset_uri` on the mention says which world answered; on the response it echoes what you sent.
- `entity.id` is your id exactly as it is in the graph or the vocabulary; `entity.same_as` carries the public identities.
- `score` is the relevance of the returned entity in [0, 1], not a calibrated probability. `signals` on each mention says how it was decided: `path` is `vocabulary` for your entities, `reranked` or `fast` for the public world; `match_probability` is the model's score for the winning pair; `retrieval_prior` is absent on your data.

## What to know before you rely on it

- **Matching is on names and aliases, exactly.** "WordLift" matches `WordLift`
  and `wordlift`; "WordLift's" or "WL" do not unless they are aliases. Add
  aliases for the forms your texts actually use.
- **Mentions the engine detects are names.** Without `mentions`, the engine's
  recognizer proposes people, organizations, places, products, works and the
  like. Concepts such as "Semantic SEO" are in your graph but are not proposed
  as mentions; pass their spans in `mentions` and they resolve normally. A
  simple scan of your text for your own names and aliases is enough to build
  that list.
- **Your entities compete with the public world.** For every match the engine
  also scores Wikidata's own candidates for that surface form, minus the ones
  your entry declares `same_as` (or, for an entry without a Wikidata id, the
  one with the same name). When one of those explains the mention better than
  any of your entities, the mention is about something outside your dataset:
  `unresolved` with `no_suitable_candidate`. "An apple a day" does not become
  your Apple; "the Amazon rainforest" does not become your Amazon. Two things
  follow: give ambiguous entries a description, because that is what the
  ranker reads; and fill `same_as` where you can, so a Wikidata twin of your
  entity never competes against it. DBpedia links count too: a public
  candidate whose Wikipedia title matches a DBpedia URI in your `same_as` is
  treated as your entity.
- **Entities without a public id still reconcile.** Most graph entities carry
  a name and a type and nothing else. For an entry that declares no public id,
  the engine takes the public entity that best fits the context and whose type
  does not contradict yours as your entity under a public id: it stops
  competing, and if your entry has no description it is judged on that twin's
  evidence. "Blue Bird" the brand in a bus listing resolves to your brand node
  because the context supports Blue Bird Corporation. An entry that does
  declare `same_as` is judged against that twin only, so filling `same_as` is
  still the precise way. The adopted twin is reported in
  `evidence.signals.public_twins`.
- **Measured, not promised.** On a development set of ambiguous names
  (`Apple`, `Amazon`, `Jaguar`, `Delta`, `Orange`, `Shell`, `Mars`, `Python`,
  `Mercury`, `Tesla`, `Visa` as companies, in company and non-company
  sentences), every positive resolved and the false resolutions on homographs
  went from all of them to two ("Apple pie" and "a jaguar was spotted") after
  this rule. Those two are the ranker over-trusting a label match; they are
  recorded, not hidden. The same rules were then measured on two real graphs
  of different nature, a publisher's and a product catalogue, together with
  that set: no wrong answer on unambiguous names in either, coverage 113 of
  126 and 85 of 87. A held-out set will gate the rules before they are called
  released.
- **A mention `type` is a constraint, not a hint.** If you pass `type` on a
  mention and it conflicts with the entity's types in your data, the answer is
  `unresolved` with `type_conflict`, even when the name matches. Rome typed
  `Organization` in a graph will not resolve for a mention typed `Place`. Omit
  `type` when your graph's types are loose, or fix the types in the graph.
- **`candidates` and a user dataset do not mix.** With a user dataset the
  dataset is the candidate world; a request that also sends per-mention
  `candidates` is rejected with 422.
- **Failures are explicit.** A graph that cannot be read is a 502; a malformed
  inline dataset or an unknown `dataset_uri` is a 422, which the clients
  raise as `InvalidRequestError` (the CLI prints `invalid_request` and exits
  1). They turn transport failures into `resolver_unavailable`, never into a
  guess.
- **Size and speed.** Inline: up to 5,000 entities per request. Graph: the
  first call reads it (a few seconds for a few hundred entities), then it is
  cached for about ten minutes per key.

## Runnable example

[`examples/resolve_with_your_data.py`](../examples/resolve_with_your_data.py)
runs all three modes against production with your key:

```bash
export WL_KEY=...
python examples/resolve_with_your_data.py
```
