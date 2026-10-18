import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
_spec = importlib.util.spec_from_file_location("voxel", TOOLS / "voxel.py")
voxel = importlib.util.module_from_spec(_spec)
sys.modules["voxel"] = voxel
_spec.loader.exec_module(voxel)
import compare  # noqa: E402  (tools/ is on the path for voxel's own import)

TTL = '''@prefix nif: <http://persistence.uni-leipzig.org/nlp2rdf/ontologies/nif-core#> .

<http://x/a#char=0,37>
a nif:String , nif:Context  , nif:RFC5147String ;
nif:isString """Die EU wächst. Eurostat meldet Zahlen."""^^xsd:string ;
nif:beginIndex "0"^^xsd:nonNegativeInteger ;
nif:endIndex "37"^^xsd:nonNegativeInteger ;
nif:sourceUrl <http://x/a> .

<http://x/a#char=15,37>
a nif:String , nif:Context , nif:RFC5147String ;
nif:isString """Eurostat meldet Zahlen."""^^xsd:string ;
nif:beginIndex "15"^^xsd:nonNegativeInteger ;
nif:endIndex "37"^^xsd:nonNegativeInteger ;
nif:broaderContext <http://x/a#char=0,37> .

<http://x/a#char=0,8>
a nif:String , nif:Context , nif:Phrase , nif:RFC5147String ;
nif:referenceContext <http://x/a#char=15,37> ;
nif:Context <http://x/a#char=0,37> ;
nif:anchorOf """Eurostat"""^^xsd:string ;
nif:beginIndex "0"^^xsd:nonNegativeInteger ;
nif:endIndex "8"^^xsd:nonNegativeInteger ;
itsrdf:taIdentRef <https://en.wikipedia.org/wiki/Eurostat> ;
itsrdf:taIdentRef <https://de.wikipedia.org/wiki/Eurostat> .
'''


class VoxelTests(unittest.TestCase):
    def test_offsets_are_the_sentence_start_plus_the_annotation_start(self):
        [article] = voxel.parse(TTL, "de")
        self.assertEqual(article["source"], "http://x/a")
        self.assertEqual(article["spans"], [(15, 23, "Eurostat", ["https://en.wikipedia.org/wiki/Eurostat",
                                                                    "https://de.wikipedia.org/wiki/Eurostat"])])

    def test_a_span_that_does_not_match_the_text_is_an_error(self):
        with self.assertRaises(ValueError):
            voxel.parse(TTL.replace('"""Eurostat"""', '"""Eurostaat"""'), "de")

    def test_own_language_link_first(self):
        links = ["https://en.wikipedia.org/wiki/Eurostat", "https://de.wikipedia.org/wiki/Eurostat", "https://x.org/y"]
        self.assertEqual(voxel.ordered_links(links, "de"), links[1::-1])

    def test_compare_reads_span_gold_and_checks_it(self):
        case = {"text": "Die EU wächst.", "lang": "de", "mentions": [{"text": "EU", "start": 4, "end": 6, "qid": "Q458"}]}
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "voxel-de_gold.json").write_text(json.dumps([case]))
            [doc] = compare.load_docs(Path(folder))
            self.assertEqual((doc.dataset, doc.gold[0].start, doc.gold[0].qid), ("voxel-de", 4, "Q458"))
            Path(folder, "voxel-de_gold.json").write_text(json.dumps([{**case, "mentions": [{**case["mentions"][0], "start": 5}]}]))
            with self.assertRaises(ValueError):
                compare.load_docs(Path(folder))

    def test_documents_over_a_systems_limits_are_not_supported(self):
        class Small(compare.System):
            name, max_chars, max_mentions = "small", 20, 1

            def answer(self, d):
                return [compare.Answer(compare.NO_ANSWER) for _ in d.gold]
        long_doc = compare.Doc("x", 0, "en", "a" * 21, (compare.Gold("x", 0, "en", "a", 0, 1, "Q1"),))
        busy = compare.Doc("x", 1, "en", "ab", (compare.Gold("x", 1, "en", "a", 0, 1, "Q1"), compare.Gold("x", 1, "en", "b", 1, 2, "Q2")))
        ok = compare.Doc("x", 2, "en", "a", (compare.Gold("x", 2, "en", "a", 0, 1, "Q1"),))
        statuses = [a.status for _, a in compare.run_system(Small(), [long_doc, busy, ok], print)]
        self.assertEqual(statuses, [compare.UNSUPPORTED] * 3 + [compare.NO_ANSWER])


if __name__ == "__main__":
    unittest.main()
