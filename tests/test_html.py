"""The HTML offset map: every clean character points at the markup it came from."""
import unittest

from resolve_pipeline.html import extract_text_with_offsets, to_html_span


def span_of(markup, word):
    text, maps = extract_text_with_offsets(markup)
    i = text.index(word)
    s = to_html_span(i, i + len(word), maps)
    return text, s, (markup[s[0]:s[1]] if s else None)


class HtmlOffsets(unittest.TestCase):
    def test_entities_are_decoded_and_map_to_their_source(self):
        text, s, src = span_of("<p>Apple &amp; Rome</p>", "Rome")
        self.assertEqual((text, src), ("Apple & Rome\n", "Rome"))
        text, s, src = span_of("<p>Apple &amp; Rome</p>", "Apple & Rome")
        self.assertEqual(src, "Apple &amp; Rome")
        text, s, src = span_of("<p>It&#x27;s Apple</p>", "It's")
        self.assertEqual(src, "It&#x27;s")

    def test_attributes_scripts_and_comments_are_never_matched(self):
        text, s, src = span_of('<div title="Apple">Apple</div>', "Apple")
        self.assertEqual(src, "Apple")
        self.assertEqual(s[0], '<div title="Apple">'.index(">") + 1)
        text, s, src = span_of("<script>Apple</script><p>Apple</p>", "Apple")
        self.assertEqual(text, "Apple\n")
        self.assertEqual(s[0], len("<script>Apple</script><p>"))
        text, _ = extract_text_with_offsets("<!-- Apple --><p>Rome</p>")
        self.assertEqual(text, "Rome\n")

    def test_leading_whitespace_is_kept_in_the_text_and_the_map_agrees(self):
        text, s, src = span_of("<p>  Apple</p>", "Apple")
        self.assertEqual((text, src), ("  Apple\n", "Apple"))

    def test_whitespace_between_inline_elements_is_one_space(self):
        text, maps = extract_text_with_offsets("<span>Apple</span> <span>Rome</span>")
        self.assertEqual(text, "Apple Rome")
        text, maps = extract_text_with_offsets("<span>Apple</span>\n   <span>Rome</span>")
        self.assertEqual(text, "Apple Rome")
        self.assertEqual(span_of("<span>Apple</span> <span>Rome</span>", "Rome")[2], "Rome")

    def test_block_boundaries_are_newlines_and_a_span_across_one_is_unmappable(self):
        text, maps = extract_text_with_offsets("<p>Apple</p><p>Rome</p>")
        self.assertEqual(text, "Apple\nRome\n")
        self.assertIsNone(to_html_span(0, 10, maps))
        self.assertEqual(to_html_span(6, 10, maps), (len("<p>Apple</p><p>"), len("<p>Apple</p><p>Rome")))

    def test_repeated_strings_map_to_their_own_occurrence(self):
        markup = "<p>Rome</p><p>Rome</p>"
        text, maps = extract_text_with_offsets(markup)
        second = text.index("Rome", 1)
        s = to_html_span(second, second + 4, maps)
        self.assertEqual(s, (len("<p>Rome</p><p>"), len("<p>Rome</p><p>Rome")))

    def test_bad_spans(self):
        text, maps = extract_text_with_offsets("<p>Apple</p>")
        self.assertIsNone(to_html_span(3, 3, maps))
        self.assertIsNone(to_html_span(0, 99, maps))


if __name__ == "__main__":
    unittest.main()
