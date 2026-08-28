import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from starunwiki.errors import IntegrityError, StarunWikiError
from starunwiki.pack import load_pack
from starunwiki.publisher import (
    PublisherContext,
    PublisherError,
    bootstrap_init,
    created_api_key_token,
    page_payload,
)
from starunwiki.release import (
    _render_sums,
    approve_pack,
    build_full_release,
    load_json,
    resolve_release_directory,
    sha256_bytes,
    verify_publishable_git_snapshot,
    verify_release_directory,
)
from starunwiki.state import StateRoot


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REPO = ROOT / "tests" / "fixtures" / "minimal-repo"


class ReleaseTests(unittest.TestCase):
    def test_minimal_fixture_builds_deterministic_v2_release(self):
        pack = load_pack("fixture-pack", repo=FIXTURE_REPO)
        first = build_full_release(pack)
        second = build_full_release(pack)
        self.assertEqual(first, second)
        manifest = json.loads(first["manifest.json"])
        self.assertEqual(manifest["schema_version"], "starunwiki.public-manifest/v2")
        self.assertEqual(manifest["release_mode"], "full")
        self.assertEqual(manifest["counts"], {"pages": 1, "draft": 0, "stable": 1, "unreviewed": 1})
        self.assertTrue(manifest["corpus"]["logical_uri"].startswith("pack://fixture-pack/releases/"))
        self.assertNotIn(str(FIXTURE_REPO), first["manifest.json"])

    def test_constructed_full_release_passes_integrity_verifier(self):
        pack = load_pack("fixture-pack", repo=FIXTURE_REPO)
        bundle = build_full_release(pack)
        manifest = json.loads(bundle["manifest.json"])
        with tempfile.TemporaryDirectory() as temporary:
            release_dir = Path(temporary) / bundle["release_id"]
            release_dir.mkdir()
            for filename in ("corpus.jsonl", "manifest.json", "profile.json", "assistant.md", "smoke.json"):
                (release_dir / filename).write_text(bundle[filename], encoding="utf-8", newline="\n")
            authorization = {
                "schema_version": "starunwiki.authorization/v2",
                "pack_id": pack.pack_id,
                "release_id": bundle["release_id"],
                "mode": "full",
                "approved_by": "operator:test",
                "approved_at": "2026-08-28T00:00:00Z",
                "approval_note": "constructed fixture",
                "source": {
                    "git_commit": "0" * 40,
                    "pack_path": "knowledge-packs/fixture-pack",
                    "content_tree_sha256": "0" * 64,
                },
                "corpus": {"path": "corpus.jsonl", "sha256": manifest["corpus"]["sha256"], "available": True, "rebuildable": True},
                "manifest": {"path": "manifest.json", "sha256": sha256_bytes((release_dir / "manifest.json").read_bytes())},
                "profile": {"path": "profile.json", "sha256": sha256_bytes((release_dir / "profile.json").read_bytes())},
                "bundle_sha256": bundle["bundle_sha256"],
                "counts": manifest["counts"],
                "exceptions": {"allow_draft": False, "allow_unreviewed": True},
                "future_changes_automatically_authorized": False,
            }
            (release_dir / "authorization.json").write_text(json.dumps(authorization, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (release_dir / "SHA256SUMS").write_text(_render_sums(release_dir), encoding="utf-8")
            result = verify_release_directory(pack, release_dir, expected_release_id=bundle["release_id"])
            self.assertEqual(result["mode"], "full")
            self.assertTrue(result["corpus_verified"])

            (release_dir / "corpus.jsonl").write_text(bundle["corpus.jsonl"] + "\n", encoding="utf-8")
            tampered_sha = sha256_bytes((release_dir / "corpus.jsonl").read_bytes())
            tampered_manifest = load_json(release_dir / "manifest.json")
            tampered_manifest["corpus"]["sha256"] = tampered_sha
            (release_dir / "manifest.json").write_text(
                json.dumps(tampered_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            tampered_authorization = load_json(release_dir / "authorization.json")
            tampered_authorization["corpus"]["sha256"] = tampered_sha
            tampered_authorization["manifest"]["sha256"] = sha256_bytes((release_dir / "manifest.json").read_bytes())
            (release_dir / "authorization.json").write_text(
                json.dumps(tampered_authorization, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            (release_dir / "SHA256SUMS").write_text(_render_sums(release_dir), encoding="utf-8")
            with self.assertRaises(IntegrityError):
                verify_release_directory(pack, release_dir, expected_release_id=bundle["release_id"])

    def test_approve_is_atomic_switch_and_rejects_duplicate_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            shutil.copytree(FIXTURE_REPO, repo)
            for command in (
                ["git", "init", "-q"],
                ["git", "config", "user.name", "StarunWiki Test"],
                ["git", "config", "user.email", "test@starunwiki.invalid"],
                ["git", "add", "-A"],
                ["git", "commit", "-qm", "fixture"],
            ):
                subprocess.run(command, cwd=repo, check=True, capture_output=True)

            pack = load_pack("fixture-pack", repo=repo)
            release_id = build_full_release(pack)["release_id"]
            old_active = load_json(pack.active_pointer_path)
            with mock.patch(
                "starunwiki.release.verify_release_directory",
                side_effect=IntegrityError("injected integrity failure"),
            ):
                with self.assertRaises(IntegrityError):
                    approve_pack(
                        pack,
                        approved_by="operator:test",
                        note="atomicity fixture",
                        allow_unreviewed=True,
                    )
            self.assertEqual(load_json(pack.active_pointer_path), old_active)
            self.assertFalse((pack.releases_root / release_id).exists())
            self.assertEqual(list(pack.releases_root.glob(f".{release_id}.*")), [])

            approved = approve_pack(
                pack,
                approved_by="operator:test",
                note="approved fixture",
                allow_unreviewed=True,
            )
            self.assertEqual(approved["release_id"], release_id)
            self.assertEqual(load_json(pack.active_pointer_path)["release_id"], release_id)
            release_dir = pack.releases_root / release_id
            self.assertEqual(verify_release_directory(pack, release_dir)["mode"], "full")

            with self.assertRaises(StarunWikiError):
                verify_publishable_git_snapshot(pack, release_dir)

            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-qm", "approve"], cwd=repo, check=True, capture_output=True)
            self.assertEqual(verify_publishable_git_snapshot(pack, release_dir)["release_id"], release_id)

            active_text = pack.active_pointer_path.read_text(encoding="utf-8")
            pack.active_pointer_path.write_text(
                json.dumps(
                    {
                        "schema_version": "starunwiki.active-release/v1",
                        "pack_id": pack.pack_id,
                        "release_id": "public-000000000000",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(IntegrityError):
                verify_publishable_git_snapshot(pack, release_dir)
            pack.active_pointer_path.write_text(active_text, encoding="utf-8")

            with self.assertRaises(IntegrityError):
                approve_pack(
                    pack,
                    approved_by="operator:test",
                    note="duplicate fixture",
                    allow_unreviewed=True,
                )

    def test_committed_legacy_release_is_manifest_only_and_immutable(self):
        pack = load_pack("deep-sky")
        release_dir = resolve_release_directory(pack)
        result = verify_release_directory(pack, release_dir)
        self.assertEqual(result["release_id"], "public-de219d707e39")
        self.assertEqual(result["mode"], "legacy-manifest-only")
        self.assertFalse(result["corpus_verified"])
        self.assertFalse((release_dir / "corpus.jsonl").exists())
        manifest = load_json(release_dir / "manifest.json")
        self.assertEqual(manifest["source_manifest_sha256"], "5ef927b252ee67300e4083972633bc6430fb7942f178412567f088f8db04e7de")
        self.assertNotIn("corpus_path", manifest)

    def test_release_rejects_non_home_absolute_paths(self):
        pack = load_pack("deep-sky")
        with tempfile.TemporaryDirectory() as temporary:
            release_dir = Path(temporary) / "public-de219d707e39"
            shutil.copytree(pack.releases_root / "public-de219d707e39", release_dir)
            (release_dir / "leak.json").write_text(
                json.dumps({"local_path": "/Volumes/private/starunwiki"}) + "\n",
                encoding="utf-8",
            )
            (release_dir / "SHA256SUMS").write_text(_render_sums(release_dir), encoding="utf-8")
            with self.assertRaisesRegex(IntegrityError, "绝对"):
                verify_release_directory(pack, release_dir)

    def test_publisher_payload_remains_wiki_only(self):
        pack = load_pack("deep-sky")
        page = load_json(resolve_release_directory(pack) / "manifest.json")["pages"][0]
        payload = page_payload(page, "public-de219d707e39", "deep-sky")
        self.assertEqual(payload["page_type"], "concept")
        self.assertEqual(payload["source_refs"], [])
        self.assertEqual(payload["chunk_refs"], [])
        self.assertEqual(payload["page_metadata"]["pack_id"], "deep-sky")

    def test_api_key_creation_uses_only_one_time_token(self):
        self.assertEqual(created_api_key_token({"api_key": "encrypted", "token": "plaintext"}), "plaintext")
        self.assertEqual(created_api_key_token({"api_key": "encrypted"}), "")

    def test_bootstrap_init_refuses_existing_remote_credentials_or_principal(self):
        pack = load_pack("deep-sky")

        class FakeClient:
            def __init__(self, *, credential_configured, principal):
                self.credential_configured = credential_configured
                self.principal = principal
                self.calls = []

            def request(self, method, path, payload=None):
                self.calls.append((method, path, payload))
                if method == "GET" and path == "/api/v1/models":
                    return {"data": [{
                        "id": "builtin-llm-wiki-chat",
                        "type": "KnowledgeQA",
                        "name": "test-model",
                        "parameters": {"base_url": "http://model.invalid/v1", "provider": "openai"},
                        "credentials": {"api_key": {"configured": self.credential_configured}},
                    }]}
                if method == "GET" and path.endswith("/api-principal-config"):
                    return {"data": self.principal}
                raise AssertionError(f"unexpected mutating request: {method} {path}")

        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            "os.environ",
            {
                "WEKNORA_TENANT_ID": "1",
                "LLM_MODEL_NAME": "test-model",
                "LLM_BASE_URL": "http://model.invalid/v1",
                "LLM_PROVIDER": "openai",
            },
            clear=False,
        ):
            context = PublisherContext(
                pack,
                resolve_release_directory(pack),
                StateRoot(Path(temporary), "canonical"),
            )
            clients = (
                FakeClient(credential_configured=True, principal={"mode": "tenant", "has_hmac_secret": False}),
                FakeClient(credential_configured=False, principal={"mode": "signed_token", "has_hmac_secret": True}),
            )
            for client in clients:
                with (
                    self.subTest(client=client),
                    mock.patch("starunwiki.publisher.validate_context", return_value={"mode": "full"}),
                    mock.patch("starunwiki.publisher.verify_publishable_git_snapshot", return_value={"mode": "full"}),
                    mock.patch("starunwiki.publisher.admin_client", return_value=client),
                ):
                    with self.assertRaises(PublisherError):
                        bootstrap_init(context)
                self.assertTrue(client.calls)
                self.assertTrue(all(method == "GET" for method, _, _ in client.calls))


if __name__ == "__main__":
    unittest.main()
