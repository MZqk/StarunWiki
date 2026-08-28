import json
import os
import tempfile
import unittest
from pathlib import Path

from starunwiki.errors import StarunWikiError
from starunwiki.state import StateRoot, migrate_legacy_state, resolve_state_root, state_report


class StateResolverTests(unittest.TestCase):
    def make_repo(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "integrations" / "llm-wiki-public").mkdir(parents=True)
        return temporary, root

    def test_empty_repo_selects_canonical_root(self):
        temporary, root = self.make_repo()
        with temporary:
            selected = resolve_state_root(repo=root, environ={})
            self.assertEqual(selected.kind, "canonical")
            self.assertEqual(selected.root, root.resolve() / ".runtime")

    def test_writable_state_inside_repository_is_only_dot_runtime(self):
        from starunwiki.pack import repository_root

        repo = repository_root()
        StateRoot(repo / ".runtime", "canonical").require_writable_canonical()
        with self.assertRaises(StarunWikiError):
            StateRoot(repo / "unsafe-state", "explicit").require_writable_canonical()
        with tempfile.TemporaryDirectory() as temporary:
            StateRoot(Path(temporary) / "external-state", "explicit").require_writable_canonical()

    def test_writable_state_root_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir(mode=0o700)
            link = root / "state-link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(StarunWikiError, "符号链接"):
                StateRoot(link, "explicit").require_writable_canonical()

    def test_legacy_and_canonical_are_selected_as_whole_roots(self):
        temporary, root = self.make_repo()
        with temporary:
            legacy = root / "integrations" / "llm-wiki-public"
            (legacy / ".env").write_text("A=legacy\n", encoding="utf-8")
            selected = resolve_state_root(repo=root, environ={})
            self.assertEqual(selected.kind, "legacy")
            self.assertEqual(selected.config_env, legacy.resolve() / ".env")
            self.assertEqual(selected.runtime_env, legacy.resolve() / ".secrets" / "runtime.env")

            canonical = root / ".runtime"
            canonical.mkdir()
            (canonical / "config.env").write_text("A=canonical\n", encoding="utf-8")
            with self.assertRaisesRegex(StarunWikiError, "同时非空"):
                resolve_state_root(repo=root, environ={})
            explicit = resolve_state_root(legacy, repo=root, environ={})
            self.assertEqual(explicit.kind, "legacy")
            self.assertTrue(explicit.explicit)

    def test_migration_copies_and_verifies_without_deleting_legacy(self):
        temporary, root = self.make_repo()
        with temporary:
            legacy = root / "integrations" / "llm-wiki-public"
            (legacy / ".secrets").mkdir()
            (legacy / ".runtime").mkdir()
            files = {
                legacy / ".env": "A=1\n",
                legacy / ".secrets" / "bootstrap.json": json.dumps({"tenant_id": 1}),
                legacy / ".secrets" / "runtime.env": "TOKEN=secret\n",
                legacy / "release-state.json": json.dumps({"schema_version": 1}),
                legacy / ".runtime" / "marker": "runtime",
            }
            for path, content in files.items():
                path.write_text(content, encoding="utf-8")
                if path.name != "marker":
                    os.chmod(path, 0o600)
            result = migrate_legacy_state(repo=root)
            canonical = root / ".runtime"
            self.assertTrue((canonical / "state-layout.json").is_file())
            self.assertEqual((canonical / "config.env").read_text(), "A=1\n")
            self.assertEqual((canonical / "runtime" / "marker").read_text(), "runtime")
            self.assertTrue((legacy / ".env").is_file())
            self.assertFalse(result["legacy_deleted"])

            layout = json.loads((canonical / "state-layout.json").read_text(encoding="utf-8"))
            self.assertEqual(layout["schema_version"], "starunwiki.state-layout/v1")
            self.assertTrue(layout["legacy_preserved"])
            self.assertTrue(layout["files"])
            for entry in layout["files"]:
                self.assertEqual(entry["legacy_sha256"], entry["canonical_sha256"])

            selected = resolve_state_root(repo=root, environ={})
            self.assertEqual(selected.kind, "canonical")
            self.assertEqual(selected.root, canonical.resolve())
            report = state_report(repo=root)
            self.assertTrue(report["preserved_legacy"])
            self.assertTrue(report["attested"])
            self.assertFalse(report["conflict"])
            self.assertEqual(report["selected_kind"], "canonical")

    def test_migration_rejects_nested_runtime_symlink_before_copy(self):
        temporary, root = self.make_repo()
        with temporary:
            legacy = root / "integrations" / "llm-wiki-public"
            runtime = legacy / ".runtime"
            runtime.mkdir()
            outside = root / "outside-secret"
            outside.write_text("must-not-be-copied\n", encoding="utf-8")
            (runtime / "escape").symlink_to(outside)

            with self.assertRaisesRegex(StarunWikiError, "拒绝符号链接"):
                migrate_legacy_state(repo=root)
            self.assertFalse((root / ".runtime").exists())
            self.assertEqual(outside.read_text(encoding="utf-8"), "must-not-be-copied\n")

    def test_migration_rejects_symlinked_static_state_parent(self):
        temporary, root = self.make_repo()
        with temporary:
            legacy = root / "integrations" / "llm-wiki-public"
            outside = root / "outside-secrets"
            outside.mkdir()
            bootstrap = outside / "bootstrap.json"
            bootstrap.write_text("{}\n", encoding="utf-8")
            os.chmod(bootstrap, 0o600)
            (legacy / ".secrets").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(StarunWikiError, "迁移路径拒绝符号链接"):
                migrate_legacy_state(repo=root)
            self.assertFalse((root / ".runtime").exists())

    def test_canonical_updates_remain_authoritative_after_migration(self):
        temporary, root = self.make_repo()
        with temporary:
            legacy = root / "integrations" / "llm-wiki-public"
            (legacy / ".runtime").mkdir()
            (legacy / ".env").write_text("A=1\n", encoding="utf-8")
            (legacy / ".runtime" / "old").write_text("old\n", encoding="utf-8")
            os.chmod(legacy / ".env", 0o600)
            migrate_legacy_state(repo=root)

            canonical = root / ".runtime"
            (canonical / "config.env").write_text("A=canonical-update\n", encoding="utf-8")
            (canonical / "secrets").mkdir()
            (canonical / "secrets" / "bootstrap.json").write_text("{}\n", encoding="utf-8")
            (canonical / "runtime" / "old").unlink()
            (canonical / "runtime" / "new").write_text("new\n", encoding="utf-8")

            selected = resolve_state_root(repo=root, environ={})
            self.assertEqual(selected.kind, "canonical")
            self.assertEqual(selected.root, canonical.resolve())
            report = state_report(repo=root)
            self.assertTrue(report["preserved_legacy"])
            self.assertTrue(report["attested"])
            self.assertFalse(report["conflict"])

    def test_legacy_drift_after_migration_remains_fail_closed(self):
        temporary, root = self.make_repo()
        with temporary:
            legacy = root / "integrations" / "llm-wiki-public"
            (legacy / ".env").write_text("A=1\n", encoding="utf-8")
            os.chmod(legacy / ".env", 0o600)
            migrate_legacy_state(repo=root)
            (legacy / ".env").write_text("A=changed\n", encoding="utf-8")

            with self.assertRaisesRegex(StarunWikiError, "legacy 迁移文件已漂移"):
                resolve_state_root(repo=root, environ={})
            report = state_report(repo=root)
            self.assertTrue(report["preserved_legacy"])
            self.assertFalse(report["attested"])
            self.assertTrue(report["conflict"])
            self.assertIn("legacy 迁移文件已漂移", report["attestation_detail"])

    def test_missing_or_incomplete_migration_marker_remains_fail_closed(self):
        for mutation, expected_error in (("missing", "state-layout.json"), ("incomplete", "文件证明集合")):
            with self.subTest(mutation=mutation):
                temporary, root = self.make_repo()
                with temporary:
                    legacy = root / "integrations" / "llm-wiki-public"
                    (legacy / ".env").write_text("A=1\n", encoding="utf-8")
                    os.chmod(legacy / ".env", 0o600)
                    migrate_legacy_state(repo=root)
                    layout_path = root / ".runtime" / "state-layout.json"
                    if mutation == "missing":
                        layout_path.unlink()
                    else:
                        layout = json.loads(layout_path.read_text(encoding="utf-8"))
                        layout["files"] = []
                        layout_path.write_text(json.dumps(layout), encoding="utf-8")

                    with self.assertRaisesRegex(StarunWikiError, expected_error):
                        resolve_state_root(repo=root, environ={})
                    report = state_report(repo=root)
                    self.assertFalse(report["attested"])
                    self.assertTrue(report["conflict"])

    def test_marker_rejects_unsafe_paths_and_initial_hash_mismatch(self):
        cases = (("unsafe_path", "不是安全相对路径"), ("hash", "初始两侧哈希不一致"))
        for mutation, expected_error in cases:
            with self.subTest(mutation=mutation):
                temporary, root = self.make_repo()
                with temporary:
                    legacy = root / "integrations" / "llm-wiki-public"
                    (legacy / ".env").write_text("A=1\n", encoding="utf-8")
                    os.chmod(legacy / ".env", 0o600)
                    migrate_legacy_state(repo=root)
                    layout_path = root / ".runtime" / "state-layout.json"
                    layout = json.loads(layout_path.read_text(encoding="utf-8"))
                    if mutation == "unsafe_path":
                        layout["files"][0]["legacy"] = "../.env"
                    else:
                        layout["files"][0]["canonical_sha256"] = "0" * 64
                    layout_path.write_text(json.dumps(layout), encoding="utf-8")

                    with self.assertRaisesRegex(StarunWikiError, expected_error):
                        resolve_state_root(repo=root, environ={})


if __name__ == "__main__":
    unittest.main()
