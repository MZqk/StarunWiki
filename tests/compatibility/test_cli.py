import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from starunwiki.catalog import corpus_summary
from starunwiki.pack import load_pack
from starunwiki.publisher import load_release_state


ROOT = Path(__file__).resolve().parents[2]


class CompatibilityTests(unittest.TestCase):
    def test_legacy_builder_keeps_v01_arguments_and_logical_paths(self):
        _, expected = corpus_summary(load_pack("deep-sky"))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "corpus.jsonl"
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "build_knowledge_catalog.py"), "--write", "--output", str(output)],
                cwd=temporary, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["sha256"], expected["sha256"])
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), expected["page_count"])
            self.assertTrue(all(not row["entry_name"].startswith("knowledge-packs/") for row in rows))
            self.assertIn("DEPRECATED", result.stderr)

    def test_v1_release_state_is_normalized_in_memory_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "release-state.json"
            original = {"schema_version": 1, "release_id": "public-de219d707e39", "kb_id": "kb", "agent_id": "agent", "model_id": "model", "page_count": 51}
            path.write_text(json.dumps(original), encoding="utf-8")
            normalized = load_release_state(path, "deep-sky")
            self.assertEqual(normalized["pack_id"], "deep-sky")
            self.assertEqual(normalized["release_mode"], "legacy-manifest-only")
            self.assertEqual(json.loads(path.read_text()), original)

    def test_root_cli_can_run_from_another_directory(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, "-m", "starunwiki.cli", "release", "verify", "--pack", "deep-sky", "--release", "current"],
                cwd=temporary, env=env, text=True, capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["mode"], "legacy-manifest-only")

    def test_legacy_state_rejects_runtime_write_or_maintenance_commands(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        env["STARUNWIKI_STATE_ROOT"] = str(ROOT / "integrations" / "llm-wiki-public")
        result = subprocess.run(
            [sys.executable, "-m", "starunwiki.cli", "runtime", "stop"],
            cwd=ROOT, env=env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("state migrate", result.stderr)

    def test_v01_manifest_accepts_corpus_parameter_and_keeps_error_exit_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            corpus = Path(temporary) / "corpus.jsonl"
            corpus.write_text(json.dumps({"entry_name": "fixture.md"}) + "\n", encoding="utf-8")
            result = subprocess.run(
                [
                    str(ROOT / "integrations" / "llm-wiki-public" / "manage.sh"),
                    "manifest",
                    "--corpus",
                    str(corpus),
                ],
                cwd=temporary,
                text=True,
                capture_output=True,
            )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("语料 SHA-256 漂移", result.stderr)
        self.assertNotIn("unrecognized arguments", result.stderr)
        self.assertIn("DEPRECATED", result.stderr)


if __name__ == "__main__":
    unittest.main()
