# Contributing

Thanks for looking. This repository is the open half of a two-part system:
the pipeline and its resolver interface are here, WordLift's resolution engine
is a separate service. Contributions that make the pipeline easier to run,
integrate or evaluate are welcome; the engine's decision rules are not
configurable from here by design.

## Setup

```bash
git clone https://github.com/wordlift/content-analysis.git
cd content-analysis
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # add [ner] for GLiNER, [html] for BeautifulSoup
python -m pytest -q
```

Tests must pass without network access, a GPU or an API key. Tests that talk
to the WordLift engine use a mocked transport (see `tests/test_resolver.py`).

## What a good change looks like

- A `Resolver` implementation for another backend: implement the protocol in
  `resolve_pipeline/resolver.py`, return `unresolved` rather than guessing when
  the backend cannot answer, and add mocked tests.
- A retriever example for a common graph store, kept in `examples/`.
- Evaluator improvements: keep the error attribution in `evaluate.py` faithful
  to the tree in the README (retrieval miss vs. abstention vs. resolution
  error). Every error must have exactly one owner.
- Gold data: one Wikidata QID per labeled mention, verified against Wikidata at
  authoring time. Say in the pull request how the QIDs were checked.

## Reporting a wrong resolution

Open an issue with the text, the mention span, the candidates you supplied (if
any), the response you got and the identity you expected. A wrong link is a
bug even when the entity is closely related; an `unresolved` where a link was
possible is a coverage report, also welcome, and treated differently.

## Licence

By contributing you agree that your contribution is licensed under the
Apache License 2.0 that covers this repository.
