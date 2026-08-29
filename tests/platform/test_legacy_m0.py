import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from starunwiki.publisher import load_release_state


ROOT = Path(__file__).resolve().parents[2]


class LegacyM0Tests(unittest.TestCase):
    def test_v1_release_state_is_normalized_in_memory_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "release-state.json"
            original = {
                "schema_version": 1,
                "release_id": "public-de219d707e39",
                "kb_id": "kb",
                "agent_id": "agent",
                "model_id": "model",
                "page_count": 51,
            }
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
                [
                    sys.executable,
                    "-m",
                    "starunwiki.cli",
                    "release",
                    "verify",
                    "--pack",
                    "deep-sky",
                    "--release",
                    "current",
                ],
                cwd=temporary,
                env=env,
                text=True,
                capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["mode"], "legacy-manifest-only")

    def test_legacy_state_rejects_runtime_write_commands(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        env["STARUNWIKI_STATE_ROOT"] = str(ROOT / "integrations" / "llm-wiki-public")
        result = subprocess.run(
            [sys.executable, "-m", "starunwiki.cli", "runtime", "stop"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("state migrate", result.stderr)


if __name__ == "__main__":
    unittest.main()
