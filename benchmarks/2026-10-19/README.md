# Entity linking compared, 19 October 2026

resolve() and four other services on two suites, scored with one rule by
[`tools/compare.py`](../../tools/compare.py): an answer counts only when its
Wikidata ID equals the gold ID.

| Suite | Documents | Mentions | What it is |
| --- | --- | --- | --- |
| [VoxEL](voxel/summary.md) | 75 | 1,005 | The same 15 VoxEurop news articles in German, English, Spanish, French and Italian, strict version (persons, organisations, places). Nobody here tuned on it. |
| [Development sets](dev/summary.md) | 126 | 351 | Our six gold sets in [`datasets/`](../../datasets/): short sentences built around ambiguous names. resolve() is developed on them, so they favour it. |

## Results

F1 (precision and recall in each `summary.md`):

| System | Mentions | VoxEL | Development sets |
| --- | --- | --- | --- |
| resolve() | given | 91.8 | 92.4 |
| Google Cloud Natural Language | found by the service | 89.9 | 89.5 |
| resolve() | found by the service | 87.0 | 91.9 |
| Dandelion | found by the service | 85.7 | 92.0 |
| Entity-Fishing | given | 77.7 | 80.1 |
| Wikidata search, first hit | the name alone | 75.5 | 82.3 |

`summary.md` has the tables, per-language F1, resolve()'s decision paths and
the head to head per mention; `report.json` has every number.

## How they ran

- resolve(): the WordLift API, build 8df08b7 (coherence among mentions), on
  19 October 2026. "Given": the gold spans; "found": the text alone, the engine
  detecting at its default threshold (0.5). The open demo gave the same 351
  answers on the development sets.
- Google Cloud Natural Language v1 `analyzeEntities`, Dandelion `datatxt-nex`
  (default `min_confidence` 0.6) and the Wikidata first hit (`wbsearchentities`
  in the document's language) on 18 October 2026; Entity-Fishing 0.0.6 run
  locally on its February 2025 data, given the spans, on 19 October 2026.
  Azure Entity Linking was not tested: it retires on 1 September 2028.
- Services that find their own mentions get the benefit of the doubt: a link on
  the gold span counts right or wrong, a link on an overlapping span counts only
  when it is right. Wikipedia answers, and VoxEL's Wikipedia labels, are mapped
  to Wikidata. A few VoxEL labels follow Wikipedia's 2018 articles rather than
  today's Wikidata items (Britain, Twitter); they are scored as written.
- The development sets are small: within one language, differences under about
  five points are noise.

## Reproduce

```bash
make setup
python tools/compare.py --datasets datasets --out results/dev
python tools/voxel.py --out results/voxel
python tools/compare.py --datasets results/voxel --out results/voxel-run
```

Each run uses the services you have keys for (see the README section
"Against other services") and lists the others. Services change; your numbers
are a new measurement, not a check of these.

## VoxEL

Henry Rosales-Méndez, Aidan Hogan and Barbara Poblete, "VoxEL: A Benchmark
Dataset for Multilingual Entity Linking", ISWC 2018,
[dataset](https://users.dcc.uchile.cl/~hrosales/VoxEL.html), licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). `tools/voxel.py`
downloads it and converts it; the three annotations per language with no
Wikipedia article (NotInLexico) are left out.
