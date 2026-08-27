import hashlib
import importlib.util
import json
import pathlib
import sys
import unittest


PROJECT = pathlib.Path(__file__).parents[1]
WORKSPACE = PROJECT.parents[1]
SCRIPT = WORKSPACE / "scripts" / "build_knowledge_catalog.py"
SPEC = importlib.util.spec_from_file_location("current_wiki_catalog", SCRIPT)
catalog = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = catalog
SPEC.loader.exec_module(catalog)


class CurrentWikiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = catalog.build_records()
        cls.authorization = json.loads(
            (PROJECT / "authorization" / "public-authorization.json").read_text(encoding="utf-8")
        )
        cls.expected_page_count = int(cls.authorization["expected_page_count"])
        cls.expected_unreviewed_count = int(cls.authorization["expected_unreviewed_count"])

    def test_current_workspace_is_the_only_public_source(self):
        self.assertEqual(catalog.ROOT, WORKSPACE)
        self.assertEqual(self.authorization["authorization_mode"], "fixed-corpus-snapshot")
        self.assertFalse(self.authorization["future_changes_automatically_authorized"])
        self.assertEqual(len(self.records), self.expected_page_count)
        self.assertTrue(all(record["entry_name"].startswith(tuple(f"{index:02d}-" for index in range(10))) for record in self.records))
        self.assertFalse(any(record["entry_name"].startswith(("raw/", "archive/", "integrations/", "services/")) for record in self.records))
        self.assertFalse(any("/Users/mz/llm-wiki" in json.dumps(record, ensure_ascii=False) for record in self.records))

    def test_llm_wiki_metadata_and_slugs_are_complete(self):
        self.assertEqual(len({record["slug"] for record in self.records}), self.expected_page_count)
        self.assertTrue(all(record["status"] in {"stable", "draft"} for record in self.records))
        self.assertEqual(
            sum(record["review_state"] == "needs-human-review" for record in self.records),
            self.expected_unreviewed_count,
        )
        self.assertTrue(all(record["source_ids"] for record in self.records))
        self.assertTrue(all(record.get("stale_after") for record in self.records))
        self.assertTrue(all(record.get("verified") is False for record in self.records))
        self.assertTrue(all(record.get("verified_by") is None for record in self.records))

    def test_generated_corpus_is_current(self):
        rendered = catalog.render(self.records)
        corpus = catalog.DEFAULT_OUTPUT.read_text(encoding="utf-8")
        self.assertEqual(corpus, rendered)
        self.assertEqual(hashlib.sha256(corpus.encode("utf-8")).hexdigest(), self.authorization["corpus_sha256"])

    def test_restricted_official_captures_are_not_in_public_corpus(self):
        corpus = catalog.render(self.records)
        capture_root = WORKSPACE / "raw/official-captures/2026-08-27-smart-telescope"
        self.assertTrue(capture_root.is_dir())
        self.assertNotIn("official-source-capture", corpus)
        self.assertNotIn("OCR 图文 1", corpus)
        for relative in (
            "pages/pixinsight/001-pixinsight-faq-16febd25.md",
            "pages/seestar/074-4k功能-98944f4f.md",
            "pages/siril/002-processing-zwo-seestar-images-2da3efce.md",
        ):
            raw_page = (capture_root / relative).read_text(encoding="utf-8")
            captured = raw_page.split("## 官网可见文本捕获\n\n", 1)[1]
            self.assertGreater(len(captured), 100)
            self.assertNotIn(captured, corpus)

    def test_restricted_market_specification_capture_is_not_in_public_corpus(self):
        corpus = catalog.render(self.records)
        capture_roots = sorted(WORKSPACE.glob("raw/site-captures/2026-08-27-starun-smart-telescope-specs*"))
        self.assertGreaterEqual(len(capture_roots), 2)
        self.assertNotIn("web-source-capture", corpus)
        self.assertNotIn("d9ed1c97dc8e65df381fbe4d1464a72ff89804c4c0dbe48fa94c5f1eabfda86b", corpus)
        self.assertNotIn("4a7c89e983f2a5b5745695b4b9274ce0706bb35b2d952748abc44755fd022b67", corpus)
        for capture_root in capture_roots:
            raw_page = next((capture_root / "pages/starun-cloud").glob("*.md"))
            captured = raw_page.read_text(encoding="utf-8").split("## 网页可见文本捕获\n\n", 1)[1]
            self.assertGreater(len(captured), 5_000)
            self.assertNotIn(captured, corpus)


if __name__ == "__main__":
    unittest.main()
