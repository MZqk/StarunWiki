import hashlib
import importlib.util
import json
import pathlib
import re
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("publisher", ROOT / "publisher.py")
publisher = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = publisher
SPEC.loader.exec_module(publisher)


class PublisherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.authorization = publisher.load_json(publisher.AUTHORIZATION_FILE)
        cls.manifest = publisher.build_manifest()
        cls.expected_page_count = int(cls.authorization["expected_page_count"])
        cls.expected_unreviewed_count = int(cls.authorization["expected_unreviewed_count"])

    def test_fixed_authorization_set(self):
        self.assertEqual(self.authorization["authorization_mode"], "fixed-corpus-snapshot")
        self.assertEqual(self.manifest["page_count"], self.expected_page_count)
        self.assertEqual(
            self.manifest["draft_count"] + self.manifest["stable_count"],
            self.expected_page_count,
        )
        self.assertEqual(self.manifest["unreviewed_count"], self.expected_unreviewed_count)
        self.assertEqual(len({page["slug"] for page in self.manifest["pages"]}), self.expected_page_count)
        self.assertFalse(self.authorization["future_changes_automatically_authorized"])
        rows, _ = publisher.load_corpus(publisher.DEFAULT_CORPUS)
        self.assertEqual(len(rows), self.expected_page_count)
        self.assertEqual(
            sum(row.get("review_state") == "needs-human-review" for row in rows),
            self.expected_unreviewed_count,
        )
        self.assertTrue(all(row.get("access") == "public_candidate" for row in rows))
        self.assertTrue(all(re.match(r"^0[0-9]-[^/]+/[^/]+\.md$", row.get("entry_name", "")) for row in rows))
        self.assertFalse(any("/Users/mz/llm-wiki" in str(row) for row in rows))

    def test_builtin_models_are_chat_only(self):
        config = (ROOT / "builtin_models.yaml").read_text()
        self.assertEqual(config.count("type: KnowledgeQA"), 1)
        self.assertNotIn("type: Embedding", config)
        self.assertNotIn("type: Rerank", config)

    def test_api_key_creation_prefers_one_time_token(self):
        self.assertEqual(
            publisher.created_api_key_token(
                {"api_key": "encrypted-at-rest-value", "token": "sk-plaintext-token"}
            ),
            "sk-plaintext-token",
        )
        self.assertEqual(
            publisher.created_api_key_token({"api_key": "encrypted-at-rest-value"}),
            "",
        )

    def test_committed_manifest_matches_source(self):
        committed = json.loads((ROOT / "authorization/public-manifest.json").read_text())
        self.assertEqual(committed, self.manifest)

    def test_corpus_drift_fails_closed(self):
        with tempfile.NamedTemporaryFile() as copied:
            copied.write(pathlib.Path(publisher.DEFAULT_CORPUS).read_bytes() + b"\n")
            copied.flush()
            digest = hashlib.sha256(pathlib.Path(copied.name).read_bytes()).hexdigest()
            self.assertNotEqual(digest, self.authorization["corpus_sha256"])
            with self.assertRaises(publisher.PublisherError):
                publisher.build_manifest(pathlib.Path(copied.name))

    def test_links_and_page_payloads_are_wiki_only(self):
        slugs = {page["slug"] for page in self.manifest["pages"]}
        for page in self.manifest["pages"]:
            for link in re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", page["content"]):
                self.assertIn(link, slugs)
            payload = publisher.page_payload(page, self.manifest["release_id"])
            self.assertEqual(payload["page_type"], "concept")
            self.assertEqual(payload["status"], "published")
            self.assertEqual(payload["source_refs"], [])
            self.assertEqual(payload["chunk_refs"], [])


if __name__ == "__main__":
    unittest.main()
