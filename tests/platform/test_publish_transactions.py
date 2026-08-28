import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from starunwiki.errors import StarunWikiError
from starunwiki.pack import load_pack
from starunwiki.publisher import PublisherContext, PublisherError, bootstrap_init, publish
from starunwiki.release import atomic_write as real_atomic_write, load_json
from starunwiki.state import StateRoot


class FakePublishClient:
    def __init__(self, *, fail_page: bool = False):
        self.fail_page = fail_page
        self.calls: list[tuple[str, str]] = []

    def request(self, method, path, payload=None):
        self.calls.append((method, path))
        if method == "POST" and path == "/api/v1/knowledge-bases":
            return {"data": {"id": "kb-new"}}
        if method == "POST" and path.endswith("/wiki/pages"):
            if self.fail_page:
                raise PublisherError("injected page failure")
            return {"data": {"ok": True}}
        if method == "POST" and path == "/api/v1/agents":
            return {"data": {"id": "agent-new"}}
        if method == "POST" and path.endswith("/api-keys"):
            return {"data": {"token": "chat-token-new"}}
        raise AssertionError(f"unexpected request: {method} {path}")


class FakeBootstrapClient:
    def __init__(self):
        self.credentials_configured = False
        self.principal = {"mode": "tenant", "has_hmac_secret": False}
        self.debug_failures = 1
        self.credential_puts = 0

    def request(self, method, path, payload=None):
        if method == "GET" and path == "/api/v1/models":
            return {"data": [{
                "id": "builtin-llm-wiki-chat",
                "type": "KnowledgeQA",
                "name": "test-model",
                "parameters": {"base_url": "http://model.invalid/v1", "provider": "openai"},
                "credentials": {"api_key": {"configured": self.credentials_configured}},
            }]}
        if method == "GET" and path.endswith("/api-principal-config"):
            return {"data": dict(self.principal)}
        if method == "PUT" and path.endswith("/credentials"):
            self.credential_puts += 1
            self.credentials_configured = True
            return {"data": {"ok": True}}
        if method == "PUT" and path.endswith("/api-principal-config"):
            self.principal = {"mode": "signed_token", "has_hmac_secret": True}
            return {"data": {"ok": True}}
        raise AssertionError(f"unexpected request: {method} {path}")

    def multipart_input(self, path, value):
        if self.debug_failures:
            self.debug_failures -= 1
            raise PublisherError("injected model debug failure")
        return {"data": {"ok": True}}


class PublishTransactionTests(unittest.TestCase):
    release_id = "public-aaaaaaaaaaaa"

    def make_context(self, root: Path, *, previous: bool) -> PublisherContext:
        pack = load_pack("deep-sky")
        release_dir = root / self.release_id
        release_dir.mkdir(parents=True)
        manifest = {
            "release_id": self.release_id,
            "counts": {"pages": 1, "draft": 0, "stable": 1, "unreviewed": 1},
            "pages": [{
                "entry_name": "00-docs/guide.md",
                "slug": "concept/docs/guide",
                "title": "Guide",
                "summary": "Fixture",
                "content": "Fixture content\n",
                "source_status": "stable",
                "source_access": "public_candidate",
                "source_review_state": "needs-human-review",
                "source_verified": False,
                "payload_sha256": "f" * 64,
            }],
        }
        (release_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (release_dir / "assistant.md").write_text("Fixture assistant\n", encoding="utf-8")

        state = StateRoot(root / "state", "explicit")
        state.secret_dir.mkdir(parents=True)
        os.chmod(state.root, 0o700)
        os.chmod(state.secret_dir, 0o700)
        bootstrap = {
            "schema_version": "starunwiki.bootstrap-state/v3",
            "status": "ready",
            "tenant_id": 1,
            "model_id": "builtin-llm-wiki-chat",
            "external_hmac_secret": "h" * 48,
            "steps": {"model_credentials": True, "model_debug": True, "principal": True},
        }
        real_atomic_write(
            state.bootstrap_state,
            json.dumps(bootstrap, ensure_ascii=False, indent=2) + "\n",
            mode=0o600,
        )
        if previous:
            old_state = {
                "schema_version": "starunwiki.release-state/v2",
                "pack_id": "deep-sky",
                "release_id": "public-de219d707e39",
                "release_mode": "legacy-manifest-only",
                "kb_id": "kb-old",
                "agent_id": "agent-old",
                "model_id": "builtin-llm-wiki-chat",
            }
            real_atomic_write(
                state.release_state,
                json.dumps(old_state, ensure_ascii=False, indent=2) + "\n",
                mode=0o600,
            )
            real_atomic_write(state.runtime_env, "OLD_RUNTIME=preserved\n", mode=0o600)
        return PublisherContext(pack, release_dir, state)

    def publish_patches(self, client):
        return (
            mock.patch(
                "starunwiki.publisher.validate_context",
                return_value={
                    "mode": "full",
                    "release_id": self.release_id,
                    "counts": {"pages": 1, "draft": 0, "stable": 1, "unreviewed": 1},
                },
            ),
            mock.patch(
                "starunwiki.publisher.verify_publishable_git_snapshot",
                return_value={"mode": "full", "release_id": self.release_id},
            ),
            mock.patch("starunwiki.publisher.admin_client", return_value=client),
            mock.patch("starunwiki.publisher.bootstrap_check", return_value={"checked": True}),
        )

    def test_m0_legacy_release_rejects_mutations_before_network_or_prompt(self):
        pack = load_pack("deep-sky")
        with tempfile.TemporaryDirectory() as temporary:
            context = PublisherContext(
                pack,
                pack.releases_root / "public-de219d707e39",
                StateRoot(Path(temporary) / "state", "explicit"),
            )
            with (
                mock.patch("starunwiki.publisher.admin_client", side_effect=AssertionError("network called")),
                mock.patch("starunwiki.publisher.getpass.getpass", side_effect=AssertionError("prompted")),
            ):
                with self.assertRaisesRegex(PublisherError, "M0 legacy release 禁止"):
                    bootstrap_init(context)
                with self.assertRaisesRegex(PublisherError, "legacy-manifest-only release 禁止"):
                    publish(context)

    def test_remote_failure_keeps_previous_runtime_and_blocks_automatic_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = self.make_context(Path(temporary), previous=True)
            old_state = context.state.release_state.read_bytes()
            old_runtime = context.state.runtime_env.read_bytes()
            client = FakePublishClient(fail_page=True)
            patches = self.publish_patches(client)
            with patches[0], patches[1], patches[2], patches[3]:
                with self.assertRaisesRegex(PublisherError, "injected page failure"):
                    publish(context)

            self.assertEqual(context.state.release_state.read_bytes(), old_state)
            self.assertEqual(context.state.runtime_env.read_bytes(), old_runtime)
            operation_dir = context.state.root / "operations" / f"publish-deep-sky-{self.release_id}"
            operation = load_json(operation_dir / "operation.json")
            self.assertEqual(operation["status"], "failed")
            self.assertEqual(operation["kb_id"], "kb-new")
            self.assertEqual(operation["pages_created"], 0)
            history = context.state.root / "release-history" / "deep-sky" / "public-de219d707e39"
            self.assertEqual((history / "release-state.json").read_bytes(), old_state)
            self.assertEqual((history / "runtime.env").read_bytes(), old_runtime)

            calls_before_retry = list(client.calls)
            patches = self.publish_patches(client)
            with patches[0], patches[1], patches[2], patches[3]:
                with self.assertRaisesRegex(PublisherError, "拒绝自动重试"):
                    publish(context)
            self.assertEqual(client.calls, calls_before_retry)

    def test_publish_rejects_symlinked_state_subdirectories_before_network(self):
        for child in ("operations", "release-history"):
            with self.subTest(child=child), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                context = self.make_context(root, previous=False)
                outside = root / f"outside-{child}"
                outside.mkdir()
                (context.state.root / child).symlink_to(outside, target_is_directory=True)
                with (
                    mock.patch("starunwiki.publisher.validate_context", return_value={
                        "mode": "full",
                        "release_id": self.release_id,
                        "counts": {"pages": 1, "draft": 0, "stable": 1, "unreviewed": 1},
                    }),
                    mock.patch(
                        "starunwiki.publisher.verify_publishable_git_snapshot",
                        return_value={"mode": "full", "release_id": self.release_id},
                    ),
                    mock.patch(
                        "starunwiki.publisher.admin_client",
                        side_effect=AssertionError("network called"),
                    ),
                ):
                    with self.assertRaisesRegex(StarunWikiError, "符号链接"):
                        publish(context)
                self.assertEqual(list(outside.iterdir()), [])

    def test_success_switches_state_and_removes_duplicate_plaintext_key_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = self.make_context(Path(temporary), previous=False)
            client = FakePublishClient()
            patches = self.publish_patches(client)
            with patches[0], patches[1], patches[2], patches[3]:
                result = publish(context)

            self.assertEqual(result["release_id"], self.release_id)
            self.assertIn("WEKNORA_CHAT_API_KEY=chat-token-new", context.state.runtime_env.read_text())
            self.assertEqual(load_json(context.state.release_state)["release_id"], self.release_id)
            operation_dir = context.state.root / "operations" / f"publish-deep-sky-{self.release_id}"
            self.assertEqual(load_json(operation_dir / "operation.json")["status"], "complete")
            self.assertFalse((operation_dir / "one-time-key.json").exists())
            self.assertEqual(os.stat(context.state.runtime_env).st_mode & 0o777, 0o600)

    def test_local_switch_failure_restores_previous_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = self.make_context(Path(temporary), previous=True)
            old_state = context.state.release_state.read_bytes()
            old_runtime = context.state.runtime_env.read_bytes()
            client = FakePublishClient()
            failed_once = False

            def fail_first_release_state_switch(path, text, mode=0o644):
                nonlocal failed_once
                if Path(path) == context.state.release_state and not failed_once:
                    failed_once = True
                    raise OSError("injected switch failure")
                return real_atomic_write(Path(path), text, mode=mode)

            patches = self.publish_patches(client)
            with (
                patches[0], patches[1], patches[2], patches[3],
                mock.patch("starunwiki.publisher.atomic_write", side_effect=fail_first_release_state_switch),
            ):
                with self.assertRaisesRegex(OSError, "injected switch failure"):
                    publish(context)

            self.assertEqual(context.state.release_state.read_bytes(), old_state)
            self.assertEqual(context.state.runtime_env.read_bytes(), old_runtime)
            operation_dir = context.state.root / "operations" / f"publish-deep-sky-{self.release_id}"
            operation = load_json(operation_dir / "operation.json")
            self.assertEqual(operation["status"], "failed-restored")
            self.assertTrue(operation["rollback_restored"])
            self.assertTrue((operation_dir / "one-time-key.json").is_file())

    def test_bootstrap_resume_reuses_checkpoint_without_rotating_credentials(self):
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
            context = self.make_context(Path(temporary), previous=False)
            context.state.bootstrap_state.unlink()
            client = FakeBootstrapClient()
            with (
                mock.patch("starunwiki.publisher.validate_context", return_value={"mode": "full"}),
                mock.patch("starunwiki.publisher.verify_publishable_git_snapshot", return_value={"mode": "full"}),
                mock.patch("starunwiki.publisher.admin_client", return_value=client),
                mock.patch("starunwiki.publisher.getpass.getpass", return_value="model-secret") as prompt,
            ):
                with self.assertRaisesRegex(PublisherError, "injected model debug failure"):
                    bootstrap_init(context)
                pending = load_json(context.state.bootstrap_state)
                self.assertEqual(pending["status"], "credentials-set")
                self.assertTrue(pending["steps"]["model_credentials"])
                self.assertFalse(pending["steps"]["model_debug"])

                result = bootstrap_init(context)

            self.assertTrue(result["resumed"])
            self.assertEqual(client.credential_puts, 1)
            self.assertEqual(prompt.call_count, 1)
            ready = load_json(context.state.bootstrap_state)
            self.assertEqual(ready["status"], "ready")
            self.assertEqual(
                ready["steps"],
                {"model_credentials": True, "model_debug": True, "principal": True},
            )


if __name__ == "__main__":
    unittest.main()
