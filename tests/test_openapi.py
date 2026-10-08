"""The OpenAPI document is internally consistent and agrees with the clients on what is optional."""
import json
import re
import unittest
from pathlib import Path

DOC = json.loads((Path(__file__).parent.parent / "docs" / "openapi.resolve.json").read_text(encoding="utf-8"))


class OpenApiDocument(unittest.TestCase):
    def test_every_reference_resolves(self):
        text = json.dumps(DOC)
        for ref in set(re.findall(r'"#/components/([a-zA-Z]+)/([A-Za-z]+)"', text)):
            self.assertIn(ref[1], DOC["components"][ref[0]], ref)

    def test_operation_security_names_a_declared_scheme(self):
        op = DOC["paths"]["/v1/resolve"]["post"]
        for requirement in op.get("security", DOC.get("security", [])):
            for scheme in requirement:
                self.assertIn(scheme, DOC["components"]["securitySchemes"])

    def test_required_fields_are_declared_properties(self):
        for name, schema in DOC["components"]["schemas"].items():
            for field in schema.get("required", []):
                self.assertIn(field, schema.get("properties", {}), f"{name}.{field}")

    def test_documented_fields_the_clients_rely_on(self):
        S = DOC["components"]["schemas"]
        self.assertIn("dataset", S["ResolveRequest"]["properties"])
        for field in ("dataset_uri", "signals"):
            self.assertIn(field, S["MentionResolution"]["properties"])
        self.assertEqual(S["ResolvedEntity"]["required"], ["id", "label"])      # types and same_as are optional
        responses = DOC["paths"]["/v1/resolve"]["post"]["responses"]
        self.assertTrue({"200", "401", "422", "429"} <= set(responses))
        self.assertIn("X-Wordlift-Consumption", responses["200"]["headers"])
        self.assertIn("X-RateLimit-Remaining", responses["429"]["headers"])


if __name__ == "__main__":
    unittest.main()
