import unittest
from pathlib import Path

from starunwiki.cli import compose_auto_migrate

ROOT = Path(__file__).resolve().parents[2]


class ComposeContractTests(unittest.TestCase):
    def test_compose_uses_app_centric_paths_and_preserves_volume_names(self):
        content = (ROOT / "deploy" / "compose.yml").read_text(encoding="utf-8")
        self.assertIn("/apps/bff", content)
        self.assertIn("/apps/web", content)
        self.assertIn("/deploy/weknora", content)
        self.assertIn("STARUNWIKI_MANIFEST_PATH", content)
        self.assertIn("AUTO_MIGRATE=${STARUNWIKI_AUTO_MIGRATE:-false}", content)
        self.assertNotIn("../../integrations/llm-wiki-public", content)
        self.assertIn("llm-wiki-public-weknora-sqlite-data", content)
        self.assertIn("llm-wiki-public-bff-sqlite-data", content)
        self.assertIn('profiles: ["legacy-databases"]', content)

    def test_builtin_models_remain_chat_only(self):
        content = (ROOT / "deploy" / "weknora" / "builtin_models.yaml").read_text(encoding="utf-8")
        self.assertEqual(content.count("type: KnowledgeQA"), 1)
        self.assertNotIn("type: Embedding", content)
        self.assertNotIn("type: Rerank", content)

    def test_legacy_release_forces_schema_migration_off(self):
        self.assertEqual(
            compose_auto_migrate(
                "legacy-manifest-only",
                {"AUTO_MIGRATE": "true", "STARUNWIKI_AUTO_MIGRATE": "true"},
                {"STARUNWIKI_AUTO_MIGRATE": "true"},
            ),
            "false",
        )
        self.assertEqual(compose_auto_migrate("full", {"STARUNWIKI_AUTO_MIGRATE": "true"}, {}), "true")

    def test_native_schema_migration_requires_verified_full_release(self):
        content = (ROOT / "deploy" / "weknora" / "run-native.sh").read_text(encoding="utf-8")
        self.assertIn('PUBLIC_RELEASE_ID is required when native schema migration is enabled', content)
        self.assertIn('release verify --pack "$pack_id" --release "$release_id"', content)
        self.assertIn('[[ "$verified_mode" == "full" ]]', content)


if __name__ == "__main__":
    unittest.main()
