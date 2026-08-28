import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from starunwiki.errors import StarunWikiError
from starunwiki.pack import load_pack, repository_root


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REPO = ROOT / "tests" / "fixtures" / "minimal-repo"


class PackPathTests(unittest.TestCase):
    def test_pack_paths_reject_absolute_parent_and_symlink_escape(self):
        pack = load_pack("fixture-pack", repo=FIXTURE_REPO)
        for value in ("/tmp/outside", "../outside", "profile/../../outside"):
            with self.subTest(value=value), self.assertRaises(StarunWikiError):
                pack.resolve(value, "test.path")

        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            shutil.copytree(FIXTURE_REPO, repo)
            outside = Path(temporary) / "outside"
            outside.mkdir()
            (repo / "knowledge-packs" / "fixture-pack" / "escape").symlink_to(outside, target_is_directory=True)
            copied = load_pack("fixture-pack", repo=repo)
            with self.assertRaises(StarunWikiError):
                copied.resolve("escape/file.md", "test.escape")

    def test_explicit_repository_root_must_name_the_root_itself(self):
        with mock.patch.dict(os.environ, {"STARUNWIKI_ROOT": str(ROOT / "knowledge-packs")}, clear=False):
            with self.assertRaises(StarunWikiError):
                repository_root()


if __name__ == "__main__":
    unittest.main()
