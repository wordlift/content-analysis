# Development gold sets

One file per language: a list of `{"text", "lang", "gold": {mention: {"qid", "type", ...}}}`.
Each labeled mention has exactly one Wikidata QID. These are development sets for
exercising the pipeline and the evaluator; they are not release holdouts.
