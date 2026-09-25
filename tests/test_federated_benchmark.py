import json
import os
import py_compile
import unittest

from tests.helpers import ROOT, read_text

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "federated_queries.json")
SCRIPT = os.path.join(ROOT, "scripts", "benchmark_federated.py")


class FederatedBenchmarkGuardTest(unittest.TestCase):
    """Cheap guards so the Phase 6 benchmark assets don't rot offline."""

    def test_query_fixture_is_valid(self):
        spec = json.loads(read_text("tests/fixtures/federated_queries.json"))
        self.assertEqual(spec.get("version"), 1)
        self.assertEqual(spec.get("baseline_source"), "openalex")
        queries = spec.get("queries") or []
        self.assertGreaterEqual(len(queries), 10)
        ids = [q["id"] for q in queries]
        self.assertEqual(len(ids), len(set(ids)), "query ids must be unique")
        kinds = {q.get("kind") for q in queries}
        self.assertIn("life-science", kinds)
        self.assertIn("computer-science", kinds)
        self.assertIn("exact-paper", kinds)
        for query in queries:
            self.assertTrue(query.get("query", "").strip(), f"{query['id']} missing query text")
            self.assertIn(query.get("profile"), ("biomed", "cs", "general"))
        profiles = {q.get("profile") for q in queries}
        self.assertIn("biomed", profiles)
        self.assertIn("cs", profiles)

    def test_benchmark_script_compiles(self):
        py_compile.compile(SCRIPT, doraise=True)

    def test_benchmark_script_not_imported_by_runtime(self):
        # The benchmark stays out of core runtime: no paperseek/paperseek_core
        # module may import it.
        forbidden = ("benchmark_federated",)
        for rel in ("paperseek_core", "paperseek"):
            base = os.path.join(ROOT, rel)
            for dirpath, _dirnames, filenames in os.walk(base):
                if "__pycache__" in dirpath:
                    continue
                for name in filenames:
                    if not name.endswith(".py"):
                        continue
                    with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                        text = fh.read()
                    for token in forbidden:
                        self.assertNotIn(
                            token, text,
                            f"{rel} runtime module references benchmark script",
                        )


if __name__ == "__main__":
    unittest.main()
