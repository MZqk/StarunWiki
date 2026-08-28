import hashlib
import json
import unittest

from starunwiki.catalog import build_records, render
from starunwiki.pack import load_pack


LEGACY_CORPUS_SHA = "de219d707e39407357b05d40c21eed58450a21f92911bf0cf449898e6daa4375"
M0_CANDIDATE_SHA = "4585dab44a298c1a6afe3501b58f3f9d19549aedf34bd920995ea522a1f22405"


class DeepSkyPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = load_pack("deep-sky")
        cls.records = build_records(cls.pack)
        cls.corpus = render(cls.records)

    def test_pack_build_has_stable_logical_paths(self):
        self.assertGreater(len(self.records), 0)
        self.assertRegex(hashlib.sha256(self.corpus.encode("utf-8")).hexdigest(), r"^[0-9a-f]{64}$")
        self.assertTrue(all(row["entry_name"].startswith(tuple(f"{index:02d}-" for index in range(10))) for row in self.records))
        self.assertFalse(any(row["entry_name"].startswith("knowledge-packs/") for row in self.records))

    def test_m0_migration_preserves_candidate_bytes_and_review_count(self):
        self.assertEqual(len(self.records), 51)
        self.assertEqual(
            sum(row["review_state"] == "needs-human-review" for row in self.records),
            51,
        )
        self.assertEqual(hashlib.sha256(self.corpus.encode("utf-8")).hexdigest(), M0_CANDIDATE_SHA)

    def test_only_formal_pages_enter_the_corpus(self):
        self.assertTrue(all(row["access"] == "public_candidate" for row in self.records))
        self.assertFalse(any(row["entry_name"].startswith(("raw/", "archive/", "profile/", "tools/", "releases/")) for row in self.records))
        self.assertEqual(len({row["slug"] for row in self.records}), len(self.records))
        self.assertTrue(all(row["stale_after"] for row in self.records))

    def test_legacy_authorization_does_not_approve_current_candidate(self):
        authorization = json.loads((self.pack.releases_root / "public-de219d707e39" / "authorization.json").read_text(encoding="utf-8"))
        self.assertEqual(authorization["mode"], "legacy-manifest-only")
        self.assertEqual(authorization["corpus"]["sha256"], LEGACY_CORPUS_SHA)
        self.assertFalse(authorization["corpus"]["available"])
        self.assertFalse(authorization["corpus"]["rebuildable"])
        candidate_sha = hashlib.sha256(self.corpus.encode("utf-8")).hexdigest()
        self.assertNotEqual(candidate_sha, LEGACY_CORPUS_SHA)
        self.assertFalse(authorization["future_changes_automatically_authorized"])

    def test_restricted_capture_markers_are_absent(self):
        self.assertNotIn("official-source-capture", self.corpus)
        self.assertNotIn("web-source-capture", self.corpus)
        self.assertNotIn("OCR 图文 1", self.corpus)


if __name__ == "__main__":
    unittest.main()
